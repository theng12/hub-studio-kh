# Changelog

Plain-language changes appear first in each release and feed the app’s What’s New. Engineering details follow under Technical notes.

## 0.6.232 — 2026-11-13
- Hub shows one menu-bar icon even if another copy starts: the second copy waits a few seconds and exits quietly.
- Update opens Studio Launcher KH. Hub never installs its own update.
- A connection failure names the address it actually tried, including the port it filled in.
- Settings says plainly what the App Update Key signs.

## 0.6.231 — 2026-11-13
- The sidebar footer now lines up: Support this app, a one-line version and What's New, a Check for Updates button, and one short status line.
- Chat shows a placeholder in the message box and a Send button that looks the same in light and dark.
- The Models page says where a model is installed and gives you the button that does it.
- Voices keeps one settings menu per voice instead of four buttons, and shows language names rather than codes.
- Overview tells you the next step when something is missing.
- Escape clears the search boxes and answers the stop and quit prompts with the safe choice.

## 0.6.230 — 2026-11-12
- Quitting Hub leaves nothing running behind it, and a second copy says so instead of taking the screen.
- Uninstalling removes the app, its helper and its login item; your data stays exactly as it was.

## 0.6.229 — 2026-11-12
- Small fixes to startup and the helper's resource guard.
- Nothing in your existing settings or profiles changes.

## 0.6.227 — 2026-11-09

- Developer checks keep their files, tools and results together, with clear reasons when a Mac cannot accept a check.
- Fleet disk information reports the space on the Mac's actual work volume. Missing information stays unknown.
- App updates use a consistent local signing identity to keep the app's identity stable between updates.

### Technical notes

- Candidate source only until the current change-aware verification, packaging, isolated launch/update smoke, feed publication and installation are verified. Portable app-gate support remains unavailable without the exact packaged registry, owner policy and qualified process-stop proof. No live policy, reservation or dispatch is enabled by this release.
- Source integrates `work/fleet-gates-20261108` at `3378fd5404080ec470ad865139d2b06ffa9c12cb` and the approved rules trim at `e0c72485525c1ff9dbc127f293bfbd0d67792060`, preserving history. Frozen gate revision 2 and existing authenticated Gateway operations remain unchanged.
- Change-aware evidence is retained under `_orchestra/hub-lead/release-gate-20261109/`: the original 347-test process exited 1; its 300 successful cases remain a passed subset, never a passing process receipt. Render then passed 47 tests from a verified internal source copy because ATLAS's group-writable ancestor is correctly refused by authority-file validation. Direct callers passed 64 tests; selected caller regressions passed 80. The exact-head 109-test inventory/service proof is reused for unchanged inputs, with its inventory module also freshly covered by the 53-test signing/inventory run at `c585e29` (exit 0, no skips). Current native checks and the separate one-test Voice shutdown check exited 0. Voice 1021 and image-smoke 3 results are reused with qualified unchanged runtime digests. Earlier failed attempts remain retained; packaging and installation are separate pending checks.
- Shared local app signing uses the normal packager; no trust, privacy or signing-key changes are made. Signature verification and designated-requirement comparisons are required before publication. Hardware qualification, remote inference/install-stop proof and cue-overlay production support are not implied.

## 0.6.226 — 2026-11-08

- Edit reference-image limits now come from the Mac that owns the model. The same mode- and count-scoped memory policy is enforced when the Hub accepts the job, when a peer Mac receives it, and in the Image worker, so a controller cannot raise or invent another Mac's limits.
- Fleet memory answers stay honest. A Mac whose RAM total cannot be read reports unknown instead of a made-up lowest tier, and a relayed memory-admission write that is still queued reports pending instead of success.
- Forwarded multipart uploads stay intact and the peer form is always closed, so reference images are not folded into the policy value and a spooled upload cannot leak.
- Render asset stores release their file descriptors even when a store is abandoned, so a long run no longer exhausts the descriptor table.
- Cue-overlay v5 ships only as a candidate: its descriptor, media validator and executor path are packaged with `production_eligible=false`. This is not cue production support, and there is no real paired MP4 proof or measured hardware/Developer Mode qualification.

### Technical notes

- Candidate only: full release gate passed at `e1c5430` R3 with all five stages exit 0, but packaging, local installation, updater feed publication and fleet rollout are NOT DONE. Release target 0.6.226 is subject to Developer release go; do not claim shipped.
- Gate evidence `_orchestra/hub-lead/fleet-batch-20261107/full-gate-e1c5430-r3-20261108/lead-verified-pass.json`: source `e1c5430126e8f0f222d4aa7b8026d4951255e297`, source clean; image-smoke 0, native 0, unittest 0 (3998 run, 32 skipped, 0 failures, 0 errors), regressions 0 (462 passed), voice 0 (1021 passed, 28 skipped).
- Producer reference policy (`08fce7b`, `0c3171c`, `b8855a1`, `8480a31`, `86f22b3`, `98d53e7`, `7aafead`, `cc1dd9b`, `9c777bd`, `7afee4b`, `c515c42`, `f6d0050`): mode-scoped RAM records and per-count reference overrides resolve against the published table and the physical tier; one validated ACK-shaped producer snapshot is carried through submit admission, the peer catalog proxy, the peer door and the Image worker; unknown, unreadable, borrowed, off or mismatching policies are refused by name, and the engine intrinsic cap only lowers the ceiling.
- Unknown memory and pending relay (`489a7c8`, `a8ea83d`, `9ce0164`, `4d41f7c`, `c5f367d`): an unreadable host total is an unknown physical tier with a named unavailable reference view rather than a fabricated `<8` tier; a held memory-admission relay returns 202 with `outcome: pending`, no readback and no replay.
- Closed forwarding (`a46d3cb`): the inserted reference policy ends with CRLF and the parsed peer `FormData` is closed in a `finally`, so every matching policy part is replaced and the remaining multipart body stays parseable.
- Abandoned render stores (`e1c5430`): `weakref.finalize` on the held-descriptor list releases the nine directory descriptors; explicit close, constructor failure and late finalizer stay idempotent and cannot close a reused fd. The abandoned-store fd test fails pre-fix (22 vs 13 fds) and passes post-fix; all 81 `RenderAssetStoreTest` cases pass.
- Cue-overlay v5 candidate (`3f13bb8`, `0df12ff`, `ec5cfd6`, `5ff5537`, `9bedfd9`, `e009c03`, `1226d84`): frozen v4 subtitle source bytes are preserved under `render_subtitle_v4`; the v5 PROFILE has `production_eligible=false` with null fidelity and hardware qualification records; the receiver validator derives 16 media facts and refuses false alpha, VFR jitter, extra streams and bounds violations; the executor admits `cue_overlay` VP9-alpha WebM under the same closed argv checks. Candidate packaging/runtime only: no cue production support, no real paired MP4 proof, and no measured hardware or Developer Mode qualification.

## 0.6.225 — 2026-11-07

- App updates run safe update checks so upgrading from 0.6.224 uses a full image first via the paired Gateway.
- YuE2 8-bit AR and bf16 NAR vocal music generation supports seven languages (English, Chinese, Cantonese, Japanese, Korean, Spanish, Russian) with English as the default.

### Technical notes

- Candidate only: full release gate, packaging, local installation, live UI verification, and fleet rollout are NOT DONE. Release target 0.6.225 is subject to gate and release go; do not claim shipped.
- Packaging external post-seal manifest and normalized payload (`2d4073c`): stages deep ad-hoc code-signed bundle (`--force --deep --sign - --timestamp=none`), strictly verifies code seal (`codesign --verify --deep --strict`), writes external sidecar manifest (`Studio Hub.app.file-manifest`) with complete byte closure, records post-seal stage digest in `output.tree-sha256`, and packages raw tree plus sidecar into read-only DMG root without mutating the sealed `.app`. Independent packaging verification PASS (31 passed, 1 skip, actual exit 0).
- Installed baseline scan nonblocking (`61c8da1`): answers release capability probe without waiting for a cold scan. Independent tests PASS (40 unit passed, 1 skip, 46 route passed; receipts inferred from successful summaries, no captured exit file).
- YuE2 vocal language contract and native view repair (`91c069b`): strips contract defaults on request-only parameters in `generation.py`, retains catalog defaults `vocal_language: "en"`, validates seven-code language enum `["en", "zh", "yue", "ja", "ko", "es", "ru"]`, removes hardcoded `yue2` fallback in `native/StudioAPI.swift`, and reads decoded contract options generically in `native/StudioViews.swift`. Focused 43 tests and native Swift typechecks PASS.
- Paired Gateway review and repair (`c0ea76c`): receipt verification report PASS (97 Python, 17 Node; lead independently verified 19/19 actual acceptance and 4/4 prepush with zero POST/PUT).

## 0.6.224 — 2026-11-06

- App updates no longer wait their turn: each Mac finishes its own running work, then installs on its own. The controller no longer reserves one shared update slot.
- Prepared support for rendering subtitles on another Mac. A real rendering check is still required before use.

### Technical notes

- Source pins: update fanout `bf5b567` (prior lead `86` on `f8812e6`, then `13` drain plus `1` route fixes, actual exit 0; independent re-review `a169c585bf7980af1` PASS, finding closed, and the actual ponytail invocation audited). The correction restores the controller's own dispatch hold and its three starvation tests; the shared update slot is removed with the legacy route granting every holder, and the node HTTP controller ask is removed. Native final own-idle checks are preserved. Remote subtitles `c845857` (compiler `1af52fc`, executor `9ef6271`, staging `bd3b746` plus freeze `9186892`, encode `bc89548` plus the correction); 205 focused tests actual exit 0 and independent review `a3ca4a8343e182bf8` PASS. The canonical v4 triple is unchanged.
- At source freeze, remote subtitle rendering support is prepared only; live Gateway EN/KM real-pixel proof was absent and production support is not claimed. The `update_drain.py` qualifier is documentation only. At source freeze the full 0.6.224 gate had not started; no build, install, real-inference proof, push, tag or release.

## 0.6.223 — 2026-11-05

- The menu bar dropdown shows the installed Hub version directly above Quit Hub Studio KH.
- Rollout coverage checks both Gateway rosters, so a site known only to 1301 cannot be missed. Shared Macs are counted once; holds, pauses and offline Macs stay visible without changing their settings.

- Image jobs carry the Models page’s free-memory override through to the worker, including reference-image jobs. Reference jobs still require their own measured minimum. If the swap monitor cannot be read, the job stops and explains why.
- Invalid job requests name the field that needs fixing. A storage error during acceptance is reported before the job enters this Hub’s in-memory queue, and diagnostics retain a bounded correlation record without request content.
- A Mac stops resending acknowledged command results even when the controller has no new commands. Removed Macs cannot recreate their old result queue, and expired results no longer appear as current.
- An update check recognises a release the site already holds, including after an earlier read failed.
- Moving work priorities drops functions this Mac no longer offers while preserving disabled functions’ saved positions. Model pickers keep the published weight precision visible. Generated-output sizes use consistent units in the same sentence.
- Settings shows this Mac’s reported Developer Mode state. The switch starts off, and test requests use the joined controller’s authenticated path. Test support stays unavailable until real inference and installation stop proofs are verified.
- Image jobs finish removing their staged reference inputs before readers can observe the terminal job state.

### Technical notes

- Source preparation only: the combined Developer Mode/controller/lifecycle lane passed 94 tests, the bug-fix lane passed 277 tests with three skips, changed Python syntax/diff checks passed, and native API/test plus all-view Swift typechecking passed. Released 0.6.222 source is merged with history retained. Native executable/UI verification, the full 0.6.223 gate, real inference/installation stop proof, packaging and installation remain pending.
- Shared guards cover submission shapes, durable-before-publication ordering, image queue/execution floors, swap-monitor availability, pressure fixtures, model-library fixture isolation, gate interpreter identity/dependency seeds, result expiry and per-read rollout reconciliation. The gate seed lock is not a reproducible environment builder or a complete dependency closure.
- Hub acceptance and external execution identity remain separate SQLite stores. A hard kill between their commits can leave a stale identity pointer; this change does not claim cross-store atomicity.
- Developer Mode node work uses the frozen studio.developer-test.v1 candidate-2 contract: exact-byte replay, revisioned persisted settings, fixed Fleet-only routes, packaged fixture/policy identities, owned-process measurement and production-first reservations. Acceptance retains receipt identities across an app update; recovery never reruns an attempt, and unknown process or download ownership keeps the physical hold. Retained logs are bounded and redacted. The harmless process-tree stop proof does not qualify model inference or installation release; positive support remains unavailable. Signed image-limit override transport is still queued.

## 0.6.222 — 2026-11-05

- Failed encoding checks can be explicitly retried without reusing an earlier attempt. Their history stays available, and an uncertain previous attempt keeps the Mac reserved.
- Image prompts use each model’s own limit: FLUX.2 Klein 4-bit 1,900 characters; Qwen Image 2.1 base and Turbo mflux 4-bit 8,000; Z-Image Turbo 4-bit 2,000.
- Image limits are published as a table for each model, job type and RAM tier, with the source beside every default. FLUX.2 Klein 4-bit allows three references at 16 GB (owner limit) and four at 24 GB and above (owner: known to run). Qwen Image 2.1 Turbo mflux 4-bit allows one reference at 24 GB and above, labelled provisional, measuring; its text-to-image jobs start at 16 GB. Reference jobs remain off at 8 GB. The table shows the strict default swap allowance and the critical-memory-pressure safety stop.
- Late-label preparation has separate capability checks and validated image assets. Older rendering permissions cannot silently enable it, and remote rendering that is off stays off.
- Closing the Hub drains both encoding-check controls before shutting down its queues.

### Technical notes

- The Image runtime pins psutil 7.2.2, required by live free-memory admission. Runtime smoke now checks a usable memory reading, so a missing dependency fails before packaging.
- Installed-model test fixtures respect the configured empty gate library, including Vision and Embeddings; fake-home negative controls verify isolation without loading real checkpoints.
- The unittest runner exits without thread-registry inspection and uses an independent C-level shutdown watchdog. A blocked shutdown prints its thread stacks and fails; it cannot silently turn green.
- Retains the shipped v1 compiler source bytes and descriptor identities. The optional late-label v3 profile is isolated; scoped encoding probes do not issue a production execution grant.
- Late-label overlay statements pass `enable` as an overlay option (`:enable='between(...)'`), and pop/pulse scale the overlay source with a per-frame `scale` filter instead of an unsupported overlay option. The real packaged FFmpeg 8.1 proof rejected the previous comma form and accepts wipe, pop and pulse; the v3 compiler and its descriptor hashes are rebuilt, while the legacy v1 triple stays byte-identical.
- Each image-limit cell distinguishes recorded measurements, owner limits and provisional values. Signed Gateway overrides are deferred to the next release. New per-count memory floors, routing and deadlines are not inferred, and the live render-memory floor remains 10 GiB pending a full-length measurement.

## 0.6.221 — 2026-11-05

- Failed encoding checks keep the Mac’s actual tool error, including missing-file or permission details, so a later read still explains the failure.
- Completed remote rendering jobs retain their own measured peak memory. Older jobs without that measurement remain unknown; the current memory admission limit is unchanged.
- Paced updates keep the health readback that held the next site. Verified rendering-switch writes are reported separately when their fleet listing has not caught up.
- Best for this hardware stays unavailable until qualified comparisons support it. Paused Macs stay paused.

### Technical notes

- Pending probe diagnostics are retained separately from settled results; unknown process state continues to hold physical capacity. Existing public probe and explicit-render schemas remain compatible.
- Revision-4 output-settings transport fixtures preserve deployed Hub v1/v2 parsing. A private capability read, including the fixed controller-to-node relay, explicitly reports Best unsupported with null profile descriptors. Selection and evidence routes remain unavailable; no temporary selection store or synthetic Best grant is created.

## 0.6.220 — 2026-11-04

- A Mac can check its encoding choices before you turn on remote rendering. Enabling it after a successful check covers only the choices that passed on that Mac.
- Failed checks leave remote rendering off and explain why. Paused Macs stay paused.

## 0.6.219 — 2026-11-04

- Remote final rendering can use an exact H.264 hardware or CPU encoding choice, including a specific bitrate cap, after that Mac has proved it can encode it.
- The Renders page shows this Mac’s render grant version and the exact encoding choices it covers.
- Existing grants stay restorable, Off stays Off, and an interrupted encoding check keeps the Mac reserved until its processes are proved stopped.

### Technical notes

- Adds authenticated exact-tuple probe control, separate immutable support evidence and reversible v2 owner grants. Probe evidence is separate from measured workload qualification; HEVC probes publish capability only.
- New internal v2 render requests bind the selected probe evidence through acceptance, claim and output receipts. Legacy v1 lifecycle shapes remain unchanged.

## 0.6.218 — 2026-11-04

- The Jobs tabs are evenly spaced and stay steady when you switch between them. Narrow windows scroll the tab bar so every tab remains reachable.

## 0.6.217 — 2026-11-03

- YuE2 8-bit keeps your chosen vocal language in the song’s style prompt. Supply lyrics in the same language; sung language quality still needs listening confirmation.
- Creator 16 GB includes YuE2 8-bit and automatically installs its required bf16 decoder.

## 0.6.216 — 2026-11-03

- Memory and disk displays use consistent 1024-based units: a 32 GB Mac reads 32 GB, and free memory, free disk and total disk show one decimal.
- Connections has Copy buttons beside network and controller addresses, including the full node join address and port. Copying addresses or tokens briefly shows Copied and leaves settings unchanged.
- Image generation offers Text to image and Reference to image, using each model’s published modes and limits. Reference to image accepts one or two images where the model supports them.
- Unsupported reference input and unknown image modes explain why the request cannot run.
- Qwen Image 2.1 mflux 4-bit models offer image sizes their runtime accepts.

## 0.6.215 — 2026-11-03

- The app is named Hub Studio KH in its window and menu bar.
- What’s New opens from beside the version at the bottom of the sidebar.
- The Fleet token stays easy to copy. This Mac’s master Hub token is tucked under Advanced with a warning to keep it private.
- SSD setup commands and guides use the renamed app folders, and the installer disk is named Hub Studio KH.
- Controller machines carry a named identity in fleet and job evidence, so remote work can be traced to the Mac that ran it.

## 0.6.214 — 2026-11-02

### Studios recover when they stop answering

Hub can restart a studio that stops answering and bring its watcher back if that watcher stops. Choosing no automatic restarts also applies to a studio that stops answering.

### Tabs stay evenly spaced

The Jobs tabs keep their spacing when you switch between empty and busy lists. Other tab bars using the same control keep stable sizes too.

### What’s New stays with the version

The app’s version and plain-language release notes are maintained together in one changelog.

### Gemma 4 QAT 4-bit uses its checked context size

Gemma 4 26B-A4B QAT MLX 4-bit now uses a 49,152-token total window, including the answer, with an 8,192-token answer cap. Its existing memory requirement stays the same.

### Kit instructions match the current installer

The kit readmes now describe Story’s install script and its embedding model. Credits are explained as a Gateway setting.

### Install SSDs use the new app folders

Kit preparation, installer lookup and drive instructions now use `1 Install Apps`, with Hub’s kit in `Studio Hub` and Gateway in `Gateway`. Preparing a drive keeps this layout.

### Technical notes

- Controller recovery: 466c64f, including fcbea7a and 144c5ca. Recover a hung Hub, restart a dead child watcher, and respect the configured recovery attempts for silent studios. These code paths do not prove the original 1300/1400 failure trigger.
- Native tab geometry: 40f980e and its branch ancestry. Owned NSSegmentedControl with fixed measured widths and per-segment accessibility; native view and transition checks retained.
- Release notes: 4475832 + 9441307. CHANGELOG.md is canonical; staging generates Resources/release-notes.md from the plain sections and refuses a version without plain changes. The engineering separator is `### Technical notes`.
- Gemma 4 26B-A4B QAT MLX 4-bit: 653b457. Manifest context 49,152 and output cap 8,192; actual-manifest boundary checks and measured evidence retained. The existing default-output reservation semantics are unchanged.
- SSD layout: kit preparation, restructuring, index stamping and active launcher paths use `1 Install Apps/<App>/`; no retired kit root is recreated.
- Kit readmes: 76f14fa + d62ac9e. Installer and embedding instructions updated; the standalone plan stops describing a second credit balance.
- Real image prompt limits remain excluded until Story 1.4.376 is installed, per Developer. No image limit or runtime change is included.

## 0.6.213 — 2026-11-01

### Studios come back after a failed start

If the Studios could not start when the app opened, for example just after an update, the app stayed open with nothing running and never tried again. It now keeps trying, quickly at first and then slowly, and says why if it can't start. If you stop or quit the Hub yourself, it stays stopped.

### An update only counts when the Studios are running

When a new version goes out to your Macs, a Mac counts as updated only once its Studios are actually up. Any Mac that doesn't get there is named, and the update carries on with the others.

### Technical notes

- **A start that fails at launch is retried (owner, 2026-11-01; sites 1300 and 1400 sat with their studios down).** `StudioController.start` only entered the recovery ladder for automatic starts, so the launch's own start gave up on its first error while the app kept answering. It now always enters the ladder, and a launch that chose not to start says why.
- **A rollout counts a Mac as updated only when its studios are up.** `services_running` must report every studio up; unknown is named, never passed. Such a Mac is named in the receipt and the rest of the rollout carries on.
- **A rollout detaches the accepted image by its device, never by a path.**

## 0.6.212 — 2026-10-30

### Voices can be tagged

A voice can now carry short tags, and they are set in the Gateway — it is the one place they are edited. The Hub and the Voice page show them on each voice, and a row of tag buttons above the list narrows it to one tag; choose the tag again, or **All tags**, to see everything again. A voice with no tags shows none, and a library from before this update loads unchanged.

### On a controller, you can see whose job is whose

Every job row now has two small tags. The first says where it runs — **This Mac**, the name of the Mac that ran it, or **Mac not reported** while it is live and the Hub has not named one. The second says who asked — **asked here** or **sent by the Gateway** — and is left out for a job filed before this update, rather than guessed.

**Overview** has the same **All / This Mac / Nodes** filter as Jobs, with the number of rows on each, so you can look at just this Mac's work or just the fleet's.

### Jobs opens on what is running

The Jobs page used to open on finished Audio rows and the running work was further down. It now starts with **Running now** — every Studio's live jobs, with their status, progress, the Mac they are on and who asked — then **Queued**, then the history for each Studio. The studio tabs and the All / This Mac / Nodes filter apply to all three.

### Cancelled jobs say so honestly

A cancelled job used to say "Cancelled by user" whoever asked. It now says **Cancelled from this Mac** or **Cancelled from the Gateway** when the Hub saw which one it was, and plain **Cancelled** otherwise. Jobs cancelled before this update show **Cancelled** too, because their record does not say who asked.

### The sidebar stops saying "fleet not read"

A controller's sidebar said **fleet not read** until somebody opened the Nodes page, even when the Mac knew its group perfectly well. It now reads the list when it connects, says **reading nodes…** while it does, and names the count after that.

### A rollout can repair a Mac that is missing the installer

When an update was sent as only the files that changed, the controller never kept the disk image, so an older Mac on that site could not install it. Running the rollout with **`--full`** now sends the whole image to a site that already has the update, which is the repair, and a second run does not send it again.

### A short line of narration is no longer refused as silence

A very short sentence can be spoken well and still measure quiet for a moment: "People could catch their breath." takes about a second and a third to say. Voice was refusing it as silent on some Macs, even though the transcribe-back heard every word. When the transcribe-back has run and heard every word of a section, a low voiced time no longer rejects it. A section with no transcript, a Khmer one, or one with a word missing is still refused, so real silence and a drifting signal are still caught.

### Studio Hub no longer looks at your drives at all

The last update stopped it reading every disk. This one removes the check itself: Studio Hub no longer looks inside any removable or network drive, at startup or later. On a Mac without a Developer ID, macOS asks for permission again after every update, and that prompt was the cause of the frozen Macs and the Macs that stopped answering. There is nothing left to ask about.

A model folder you choose yourself under a drive is still read, because you chose it.

### Installing models from an SSD

Open the drive and run **2 - Copy the models.command**. It lists the four sets — **Essentials 8 GB**, **Creator 16 GB**, **Creator Plus 24 GB**, **Studio Pro 32 GB+** — and copies the one you pick, plus anything it needs, and only what is missing. The Terminal may ask once whether it may read the drive; that is your own action, not the app running by itself. **2 - Copy the models (everything).command** still copies every model the Mac can run, for a Mac with no internet and no other Mac to copy from.

### A result that arrives too late is kept out

Sometimes a Mac goes quiet and the Hub cannot tell whether a job finished. After half an hour it releases the Mac so later work can run. Now it also keeps the job's identity and writes down that the attempt was discarded, so a result that turns up later cannot replace the cancelled job or a newer finished one. Nothing is lost quietly.

### Updating the fleet while it is working

Publishing an update only makes it available; each Mac installs it when its own work finishes. A new option lets the rollout publish to a site that is busy instead of waiting for a completely quiet site. It is off unless asked for.

### Technical notes

- **A voice's tags are kept, shown and filterable, and the Gateway is their only editor.** The Gateway sends a JSON array in the multipart `tags` field of `POST /api/hub/shared-voices` (its commits `56c7b7b`, `a699bd4`); the Hub stored nothing and now stores them in `shared_voices` (`_tags`: trimmed, de-duplicated, at most 12 of at most 24 characters; a missing or malformed field is **no tags** and never refuses the voice), answers them on `serialize`, and fans them to every Voice Studio in the `_sync_target` payload. The Voice Studio's `Voice` record gains `tags` (read-only here), `sync_from_hub` keeps them, and the Voice page shows each tag as a dashed chip and filters the grid by one. The native Voices page decodes `tags` as an optional list (absent on an older worker), shows chips beside the Fleet badge, and has the same filter with counts. Nothing in the Hub edits a tag. **Proof:** `tests/test_shared_voices.py` (26; store, fan-out, omitted/empty refresh, older voices, malformed and out-of-bounds fields), `tests/hub_regression/cases/test_shared_voice_tags.py` (2; the Gateway's multipart push through the real route and auth), `components/voice/app/tests/test_fleet_voice_sync.py` (6; install keeps the Gateway's tags, an older voice loads with none).

- **A short section whose every word was heard is no longer refused as silent (owner report, 2026-10-30; EP0098 blocked).** `tts_quality.check_section` refused with `TTS_SECTION_SILENT` whenever `voiced_s` was under `min_voiced_s`, which the voiced-time *floor* cannot distinguish from real silence. "People could catch their breath." (32 characters) has a **1.09 s** floor and measures 0.82-1.04 s voiced over 1.36-1.44 s on Breeze with voice Mark, and Whisper heard all five words - it failed on **12 renders across several Macs**. When the transcribe-back ran, the text is not Khmer, and every expected word was heard (`matched >= total`, `total > 0`), a voiced time under the floor no longer rejects. Thresholds are unchanged, and the refusal still applies when `heard` is `None` (Whisper skipped or over budget), on Khmer (advisory transcript), when a word is missing, and over near-silent audio whose transcript does not match. Tests in `components/voice/app/tests/test_tts_quality.py` pin the measured case at both ends of the spread, and the four refusals.
- **The Hub no longer reads any removable or network drive, at start-up or anywhere else (owner, 2026-10-30).** 0.6.211 still opened a kit-named drive at start-up so the install screen could name the mounted kit, and on an ad-hoc signed build that read is what macOS asks about after every update. The whole automatic check is gone: `components/hub/backend/removable_volumes.py`, its start-up child, its cached status, the `removable_volumes` field on `/api/health`, `/api/hub/resources` and fleet rows, `model_install_plan`'s `kits`/`kits_state`, the native `NativeRemovableVolumes` model, its "needs permission" notes and the Setup-page sentence. The removed probe and its tests are in `_trash/removed-volume-probe-0.6.212/`, not left dead. A Hub reading a model library the person chose under `/Volumes` is unchanged, with its existing warning; nothing reads it unless it was chosen.
- **An SSD install is the drive's own command, run by a person.** **`2 - Copy the models.command`** now lists the four presets and copies the chosen one, carrying its companions, and copies only what is missing. It is the only SSD path, so the Hub never needs to see a drive: macOS may ask its permission in the Terminal window, once, because a person started it. **`2 - Copy the models (everything).command`** keeps the no-internet/no-peer path. Kit readmes, `START-HERE.md`, `NODE-SETUP-CHECKLIST.md` and the launcher comments say the same thing. `packaging/stage_voice.py` now stages `model-presets/` into the bundle, which the command needs to offer the presets.
- **A late worker result after an uncertain attempt is cleaned up is discarded, not silently lost.** `broker.discard_uncertain_item` keeps the attempt's worker identity (`studio`, `studio_job_id`, `worker_id`, revisions) and records `recovery.phase = "discarded"` with the reason, while still releasing the machine. `_record_worker_success` refuses to adopt a result for an item whose attempt was discarded, so a result arriving after the 30-minute automatic cleanup cannot overwrite the cancelled item or a newer accepted result. Tests: late result after cleanup, newer result present, and no late result.
- **A full rollout push gets the whole two-hour window, and a timed-out write is checked rather than declared failed.** Carried from `982de7b`: `PUBLISH_TIMEOUT_S` is the rollout window (2 h), `--publish-timeout` bounds it explicitly, a Gateway POST that times out returns a recorded outcome instead of an exception, and after a non-outcome reply the tool reads the controller once — if it already holds the release the site is accepted as `RECOVERED_HELD`. Evidence: a recorded 0.6.204 full push took 36 minutes against the old 30-minute timeout.
- **`--allow-busy` publishes to a reachable site that is working, and is off by default.** Publishing only stages the release on the controller; it starts no install. Every Mac — the controller included — installs from its own update tick only after its own work finishes, so the offer cannot interrupt a running job. The option still refuses an unreachable controller or an unreadable Gateway gate, and the site's row says it was published while busy. The default remains the quiet-site gate.
- **Proof.** `tests/test_copy_models_for_this_mac.py` pins preset choice, cancel, invalid choice, and that a preset moves nothing the Mac already has; `tests/hub_regression/cases/test_discard_uncertain.py` adds the three late-result cases; `scripts/test_hub_rollout.py` adds the busy-site option and its refusals (39 tests). `tests/test_hub_model_presets_route.py` no longer has a kit source.

## 0.6.211 — 2026-10-30

### No more "Allow" on a Mac that has no kit drive

Every time Studio Hub started, it looked inside every drive attached to the Mac — it was looking for the SSD kit. On a Mac with no kit attached, the only thing that look could find was somebody else's disk or a mounted network share, and macOS asks for permission to read those. On a build without a Developer ID the Mac asks again after every update, so the prompts kept arriving on Mac after Mac for an answer the app never needed.

Studio Hub now asks the Mac **which** disks are mounted — that list is free, it reads no drive — and looks inside only a drive whose name begins with **KH-STUDIO-**, the name every kit drive has. A Mac with no kit drive is not read at all, so macOS has nothing to ask about.

Two things worth knowing. A kit drive that has been **renamed** is no longer found automatically, so keep the KH-STUDIO- name. And a kit drive shared over the network is not read either, because that is the other permission Studio Hub is avoiding.

### Lite Mode is set up like any other Mac

The extra **Copy the Lite Mode models** step is gone from the kit. A cashier Mac is installed and set up like any other Mac — take **Essentials 8 GB** on **Set up this Mac**, which carries every model Lite Mode uses — and then tick **Lite Mode** in Settings. Lite Mode itself works exactly as before: it runs only its own short list of models and hands everything else to another Mac.

### The Setup page says which "no kit" it means

"No SSD kit is mounted" now appears only when that is what happened. If a KH-STUDIO- drive is mounted but has no model-library folder on it, the page says that; if the drive with that name is a network share, it says that. A drive the Hub is not allowed to read still shows **needs permission (Removable Volumes)**.

### Technical notes

- **A Mac with no SSD kit attached no longer reads any volume, so macOS has nothing to ask about (owner, 2026-10-30).** 0.6.210 stopped the freeze by moving the `/Volumes` walk into a bounded child process, but that child still opened **every** mounted volume at every Hub start. The walk exists only so the install screen can say which SSD kit is mounted, so on a Mac with no kit attached the only thing it could find was somebody else's disk — a USB drive ("access files on a removable volume") or a mounted share ("access files on a network volume"). An **ad-hoc signed** build loses that permission on every update, so the owner clicked Allow on Mac after Mac for an answer the app never needed. The child now decides **which** volumes to open from `/sbin/mount` — the kernel's own list, which reads no volume — and opens nothing else. A mount whose name does not begin **`KH-STUDIO-`** is never touched, and a network filesystem (`smbfs`, `nfs`, `afpfs`, `cifs`, `webdav`, `autofs`, `devfs`) is never touched even when it carries the kit's name, because that is the *other* permission this fix exists to avoid. `/Volumes` itself is no longer listed at all: 0.6.210 still did `Path.iterdir()` on it and `Path.is_dir()` on every entry.
- **The name is the only thing about a drive the Hub may learn without reading it.** The scan of a volume root is exactly what macOS asks about, so there is no "look inside to see whether it is a kit" step before the permission exists. `KIT_VOLUME_PREFIXES = ("KH-STUDIO-",)` records the naming every drive the kit has been written to uses (`packaging/prepare-kit.sh KH-STUDIO-XXXX`); widening it is one edit there. **The limit, stated plainly:** a kit drive that has been renamed is not found automatically, and the Models page's own folder choice is unaffected. Nothing about that is silent — the answer carries `looked_inside`, so "I was not allowed to look" and "there is no kit" cannot be read as each other.
- **Hidden entries are never entered.** macOS keeps protected metadata folders on every volume (`.DocumentRevisions-V100`, `.Spotlight-V100`, `.TemporaryItems`, `.Trashes`); measured on this Mac, reading them returns **EPERM**. The old `glob("*")` never matched them, and the first draft of this change did — which recorded four healthy kit drives as "could not be read". The child now skips names beginning with a dot, and a test pins it.
- **Honest states, in the shape this repo requires.** `probe()` publishes `looked_inside` (the drives it read), `unreadable` (a path and its error, when a kit-named drive or a folder inside it refused) and `not_read` (a kit-named mount on a network filesystem). `needs_permission` is kept for "I could not tell": a kit-named drive macOS refused, a timeout, a dead child or an unreadable answer. `ready` with no kits now says **which** answer it is — no drive named like a kit is mounted (nothing was opened), a kit drive is mounted but holds no `model-library`, or the only mount with that name is a share — instead of an empty list that reads as "none here". A refused drive beside a healthy one stays `ready` with the healthy kit, and the reason names the drive that was not read.
- **The install screen shows the Hub's sentence.** `native/StudioViews.swift` uses `kits_state.reason` when no kit was found, so "No SSD kit is mounted" is shown only when that is what happened; a renamed kit drive, a kit drive with no library, or a share with the kit's name each say their own thing. `needs permission (Removable Volumes)` is unchanged. No other app behaviour changed and no `native/` file gained a new data field.
- **The Lite Mode copy step is gone from the kit (owner, 2026-10-30).** `copy-lite-models.command` — and the drive's **"2 - Copy the Lite Mode models.command"** — is deleted rather than retired: since 2026-10-28 it copied nothing (it printed a note and ran the ordinary copy step), the owner has moved it to `_trash` on every drive, and nothing in `packaging/` wrote it any more. A Lite Mode Mac is set up like any Mac and takes the **Essentials 8 GB** preset on **Set up this Mac**, then ticks Lite Mode in Settings. **Lite Mode itself is unchanged:** `components/workstation_models.py` stays the runtime allowlist, the Hub still refuses any other model at a Lite Mode Mac's door, and Settings > Lite Mode is untouched. Removed with it, because nothing else called them: the `STUDIOHUB_WORKSTATION` filter in `qualification/copy_models_for_this_mac.py` (no file has set that variable since 2026-10-28, so the branch was unreachable) and `tests/test_shop_kit.py` (its two subjects were exactly that launcher and that filter). `packaging/prepare-kit.sh` now moves a drive's leftover copy to that volume's `_trash` on its next build — never deletes it — and the kit README, `NODE-SETUP-CHECKLIST.md`, `prepare-kit-extras.sh`, `components/hub/workstation.py`, `components/workstation_models.py` and `AGENTS.md` no longer describe a Lite-only install set. `packaging/release_kit.py` never shipped the file; its one Lite Mode sentence describes the surviving feature and stays. Both removed sources are in this worktree's `_trash/retired-lite-mode-kit-20261030/`.
- **Proof.** `tests/test_removable_volumes.py` (**20 cases**; 0.6.210's file had 11). New: no drive is opened when none is named like a kit — with **model-library folders planted inside the non-matching drives**, so a content walk would have found one, and an **audit hook** recording every path the child opens, stats or scans proves the plain drive, the network share and `/Volumes` itself were never touched; a network volume named `KH-STUDIO-*` is never read; the three-level kit layout (and both older layouts) is found inside a kit-named drive; a kit drive holding no library says so; a kit drive macOS refuses is `needs_permission` with the drive named and `Click Allow`; one refused drive does not hide the kit that was found; macOS's hidden metadata folders are skipped even when unreadable; the production root `/Volumes` is never listed; the cached answer carries `looked_inside`; and `/sbin/mount` names the root filesystem. The audit-hook cases also assert the hook recorded the drive it *did* read, so "nothing was read" cannot pass on an empty log. **Live, on this Mac, with the four `KH-STUDIO-*` drives plus `/Volumes/Recovery` and a mounted disk image (`/Volumes/Shottr`) mounted:** the 0.6.210 child scanned **all six**; the 0.6.211 child scanned the **four kit drives only** and found the same four libraries, with `Recovery` and `Shottr` untouched. The child takes **45–103 ms** with those four drives attached, against its 2 s budget (measured three times on this Mac). Evidence `_orchestra/hub-0211-release-20261030/live-volume-proof.json` (script beside it).

## 0.6.210 — 2026-10-30

### A Mac no longer freezes when macOS asks about the model drive

Some Macs stopped answering — the app looked frozen and every screen waited — and came back the moment somebody clicked **Allow** on a macOS prompt about removable drives. That prompt was the cause. macOS asks every copy of Studio Hub whether it may read a removable volume, and an unsigned app is asked again after every update. One read of the drive list waited on that unanswered prompt and held the Hub up.

Studio Hub now checks the drives **once, in the background, with a time limit**, and every screen reads only that answer. A Mac waiting on the prompt keeps serving work; it simply cannot see the SSD kit until you click Allow.

### It says what is waiting

When the permission is missing, the app now says **needs permission (Removable Volumes)** — in the banner, on the **Set up this Mac** screen, and on that Mac's row in the fleet — instead of looking busy or offline. Click **Allow** when macOS asks, then restart the services. Nothing else is affected: models can still come from another Mac or the internet.

### Check for update now

There is a **Check for update now** button beside the version in the sidebar (and on **Connections → App updates**). On any Mac — controller or node — it asks the controller straight away whether a newer release is held. If work is running it says it will update when that work finishes; it never restarts in the middle of a job. The answer is one plain line: up to date, updating, or why it could not check.

### The kit instructions match what the app does

**START-HERE.md** now describes the real setup: install the app, then choose models in **Set up this Mac**. It covers the no-internet copy step, the retired Lite Mode copy step, and the Removable Volumes prompt.

### Technical notes

- **A Mac no longer freezes when macOS asks for permission to read a removable drive (owner diagnosis at the Macs, 2026-10-30).** 0.6.206 added the first walk of `/Volumes`; it ran inside `GET /api/hub/model-presets`, and the app read that route from `refresh()` on every connect and every poll. An **ad-hoc signed** build loses its macOS permission on every update, so the blocked `open()` waited on an unanswered prompt and held a threadpool slot until `/api/health` and `/api/hub/resources` stopped answering — the observed freeze. Clicking **Allow** revived the Mac, which is how the owner found it. 0.6.209 still had it. The owner decided against a Developer ID and any signing change, so the fix is in how the walk runs.
- **The walk is one child process with a hard timeout, once per process, never on a request.** New `components/hub/backend/removable_volumes.py`: `start()` (called from the Hub's lifespan) runs the listing in a child process, kills it after `PROBE_TIMEOUT_SECONDS` (2 s), and caches the answer; `status()` and `kits()` only read that cache and never block. A timeout, a refusal, a child that dies and an unreadable answer are all the honest state **`needs_permission`** with the reason — never an empty kit list, which would read as "no SSD is mounted". It also finds the library where the 2026-10-28 drives actually keep it (`<drive>/Video Generation Kit/Studio Hub KH/model-library`); the old two-level walk missed that layout, so the screen could not offer the SSD as a source on the drives the kit ships on. Both older layouts still work. `model_install_plan.snapshot()` now takes `kits` (and `kits_state`) as arguments and has no `/Volumes` access at all, so a route cannot reintroduce the walk.
- **The honest state is published and shown.** `resources.host_stats()` carries `removable_volumes` beside `connection`; `/api/health` carries the same cached object (it must keep answering during exactly this fault); every `/api/hub/fleet/overview` row carries it. The app reads `/api/health` in its refresh and shows **"needs permission (Removable Volumes)"** in `startupNote`, on the Setup page, and on a fleet row, distinct from busy and from unreachable — a Mac with an unanswered prompt is serving, it just cannot see the kit. `ShellCore.swift`'s `protectedFolderWarning` now covers `/Volumes` for the same reason it covers `~/Downloads`: that permission refusal is a wait, not an error.
- **An idle Hub reads no model library.** The cleanup timer called `model_removal.handoff()` every pass, and `handoff()` lists `<library>/_trash` — a protected read on an external drive. It now runs only when `model_removal.removal_happened()` is true: a removal in this process, or a small marker in the Hub's own data folder left by one (so a Mac that restarted still finishes its 30-day handoff). No removal, no read.
- **The app reads the install screen on demand, not on the poll.** `/api/hub/model-presets` moved out of `refresh()` into `loadInstallPlan()`, called when **Set up this Mac** opens and after an install or removal. The poll still reads the package rows and `/api/health`; it never asks for the install screen.
- **The Gateway's per-node preset route is off the event loop.** `GET /api/hub/fleet/nodes/{id}/model-presets` called the synchronous `model_presets_screen()` directly from an `async` route; it now runs in `run_in_threadpool`, so a slow read cannot stall every other coroutine — the same shape as the freeze.
- **"Check for update now" on every Mac (owner, 2026-10-30: *"add a way to probe for update when I'm on the node machine."*)** A button beside the version in the sidebar and on **Connections → App updates** asks the controller immediately (clearing the ten-minute release-check interval), then uses the **existing** verified install path — the same key pin, controller signature, byte and delta-or-image checks. With work running it says *"Will update when the current work finishes"* and lets the normal drain finish it; nothing is restarted mid-job. The result is one plain sentence: `Up to date (0.6.210)`, `Updating to 0.6.2xx…`, `Can't reach the controller: …`, or the volumes-permission note.
- **Kit documents follow the current setup flow.** `qualification/START-HERE.md` describes **1 - Install the app** then the app's **Set up this Mac** screen, the no-internet `2 - Copy the models (everything).command`, the retired Lite Mode copy step, and the Removable Volumes prompt. `packaging/kit-readmes/` no longer carries the 0.6.90 / 1.4.235 / 0.2.67 fallbacks. `packaging/prepare-kit.sh` is now **tracked** and derives `ROOT` from the checkout it is run in, so a release worktree prepares drives from its own sources instead of the hard-coded primary checkout (the shared `.build/prepare-kit.sh` carries the same line).
- **Proof.** `tests/test_removable_volumes.py` (**10 cases**) pins: a child that blocks forever times out into `needs_permission` without blocking the caller; the result is cached and the child runs once per process; a refused read is `needs_permission`, not an empty list (a typed state, not a zero); with a blocking child running, `/api/health`, `/api/hub/resources` and `/api/hub/model-presets` all answer and `host_stats()` reports `needs_permission`; no route calls the probe; `snapshot()` requires kits; an idle cleanup reads no library while a real removal does; and a slow preset screen does not hold the event loop. `tests/native/api_main.swift` pins that the poll never reads `/api/hub/model-presets`, that opening the screen reads it once, that a `needs_permission` state becomes the banner note and clears again, and all four results of **Check for update now** (up to date, unreachable, waits for work, installs when idle through the stubbed verified path). `tests/native/main.swift` pins the `/Volumes` warning. The gate's required reproduction — a removable volume mounted **and** an access blocking, with every route still answering — is in the first test above, and a live run on this Mac with the four `KH-STUDIO-*` drives mounted is recorded in the handoff.

## 0.6.209 — 2026-10-29

### A direction for how a line is read — and the voice you cloned stays itself

A voice request can now carry a direction for one piece of text: "speak slowly and sadly, voice close to breaking", "warm and reassuring", "like you are telling a secret". A voice you cloned still sounds like itself while it reads that way; before this, it read the words plainly.

Each voice model takes a direction in its own way, and the app now states which way that is, together with the model's own examples, so the direction you write lands the way you meant. Breeze TTS 2 takes it as a performance note over your reference clip. VoxCPM2 takes it as a short style note in brackets in front of the words, and puts it in front of every section of a long script, so the mood does not drift away after the first minute. A voice model that cannot take a direction refuses the request and says why, rather than quietly ignoring it and handing you a render that did not do what you asked.

A direction belongs to that one request and is never saved as a default, so it cannot colour a later piece of text you did not mean it for. Every finished job also records the direction it ran with and what the engine was actually given, so two takes can be told apart.

### Not on the Voice page yet

The Voice page still has its older style box, which a cloned voice does not read, and it has no box for the new direction in this build. For now a direction is sent with the request by whatever calls the Hub — the Gateway, a script, or another app — and the Studio's Voice page will get a box for it in a later version. The Voice page does show, on each voice model, that it takes a delivery direction.

### A direction does not replace a voice you described

Breeze TTS 2 has one place to put a performance note. If one request asks for both a described voice and a direction, the direction is what it performs, and your voice description is still kept in the job's record. Choose one or the other if you want a described voice read plainly.

### Technical notes

- **One per-request direction for how a line is read, in each engine's own documented form (owner, 2026-10-29).** A Voice request may now carry `delivery_instruction` — *"Speak slowly and sadly, voice close to breaking."* — up to **500 characters** (`lane_contract.DELIVERY_INSTRUCTION_MAX_CHARACTERS`, published on the request model and quoted by the catalogue's guidance from that one constant). It is **request-only**: never an operator default, never stored in a preset, and an omitted or blank value is dropped from the job's recorded parameters so a retried `client_request_id` still compares equal to its stored request. The engines take it differently, so the form is data on the family (`catalog.Family.delivery_instruction`) and never inferred from a model's name: **`instruct`** puts the direction in the engine's own `instruct` argument, **`text_prefix`** puts it in front of the text as a `(style)` prefix. A family that states no form refuses the field.
- **Breeze TTS 2 — `instruct`, beside the reference clip.** `components/voice/app/backend/generation.py` passes the direction as `instruct` together with `ref_audio` and `ref_text`, so a **cloned** voice is directed without changing who it is; the mode label becomes `clone + direction (voice=…, N char prompt)`. With no reference, a design prompt and a direction compete for the engine's **one** `instruct` key and the delivery direction wins — it is the more specific, per-request instruction, and the design prompt is still in the job's recorded parameters, so the loser is visible in the record rather than silently dropped. The card's own **`cfg_scale` 4** for a directed request is applied through `breeze_cfg_scale`, and only while an instruction is present: a value the request supplies still wins, and an explicit `null` counts as omitted, because that is what null means everywhere else. The card's four inline vocal events (`(sigh)`, `(laugh)`, `(cough)`, `(clears throat)`) ride in the **text**, not the instruction, and its language rule ("Match the instruction language to the target text") is published as the card states it.
- **VoxCPM2 — a `(style)` prefix in front of the text.** Each rendered section carries `(style)` immediately before its words — exactly the card's `(slightly faster, cheerful tone)This is a cloned voice with style control.` — so the style does not fade after the first section. Parentheses in the instruction are removed **for this form only**, because a bracket inside the style could otherwise close the prefix early and turn the rest of it into script text. The caller's original text is what the job records; `serialize_model`'s guidance states what was sent. The card states no language rule and documents no inline event syntax, so none is published and none is offered — an invented tag would be read aloud as a word.
- **A model that cannot take one refuses it, loudly.** `catalog.delivery_instruction_refusal` is checked on `POST /api/generate/txt2speech` and on the reference-clone route (both answer **400** with the reason), and again in `GenerationManager.start` before a job exists, so any other caller gets the same rule. Accepting the field and dropping it — a successful generation that ignored what was asked — is the one outcome this cannot have.
- **The direction a render ran with is recorded, end to end.** The worker writes `delivery_instruction` into the job result: the caller's own `text`, the `sent_text` the engine actually received (they differ for the prefix form), the `engine_form`, the engine argument or prefix, and the `cfg_parameter` / `cfg_scale` used. The Hub copies it onto the batch item and into its terminal result (`broker.terminal_result`), and `_shared_params_with_local_reference` lifts a `delivery_instruction` sent beside `model` into `sharedParams` instead of dropping it while the job still renders — the same rule that already applies to `voice_reference_asset_id` and `voice_library_id`.
- **Published, so a caller can write one.** The model row now carries the capability **`delivery-instruction`**, derived from the family's engine (`capabilities_for`) rather than copied into every checkpoint's row, plus a `delivery_instruction` guidance object under `controls`: the form, the 500-character bound, up to three card examples, the card's language rule, the inline events and where they go, the directed `cfg` value and the request field it belongs to, the `source_url` of the model card, and the note. `components/hub/backend/capabilities.py` re-publishes that shape to the Gateway with every value re-bounded and drops a row that names no form, no `https` source or no positive bound — nothing here invents a number. `components/voice/app/frontend/app.js` shows the capability as "delivery direction".
- **No app screen sends it yet.** This is a Hub/worker request field plus the published capability and guidance: the Voice page still sends its older `instruct` style box (which a cloned voice does not read) and has no input for `delivery_instruction`, and no `native/*.swift` file changed. The one visible change in the app is the "delivery direction" capability label on a voice model. A box on the Voice page is a later version, not this one.
- **Proof.** `tests/test_delivery_instruction.py` — **36 cases in 11 classes**: a clone carrying the instruction beside the reference; the card's `cfg_scale` recorded and a request-supplied value kept; no instruction leaving the plain defaults in force; the direction winning over a design prompt; the prefix in front of the text with the caller's script unaltered and the record keeping both texts; parentheses removed for the prefix form only; refusal by name in the catalogue, before a job exists in the manager, and as a 400 on both routes; the request model and the published guidance quoting the same bound; and the two unchanged-without-an-instruction cases. The families' weights are not on every Mac, so these prove the contract at module level rather than by a render. **One thing in that file was wrong and is fixed here:** two of its relay cases put a batch into the module-level `broker.batches` and never removed it, so `/api/hub/jobs` listed it to every later module in the gate's one-process run; both now clear through `addCleanup`, and the first gate attempt (which failed on exactly that) is kept as evidence.

## 0.6.208 — 2026-10-28

### A Studio that stops answering says so quietly — and only when it matters

A notice used to appear across the top of every page saying "A studio did not answer", without saying which one. It stayed there until you dismissed it, even after the Studio had answered again, and it appeared for Studios you had switched off, which were never going to answer anything.

That notice is gone. If a Studio this Mac is meant to serve really does stop answering — not one missed poll, but several in a row — a small line now appears in the sidebar next to "Local services ready", naming it: "Voice Studio not answering", or "Subtitle Studio still starting" while it is coming up. Hover over it, or click it, for the whole story. It clears itself the moment the Studio answers. A Studio you have switched off, or one this Mac does not run, stays silent.

### The other notices clear themselves too

A note at the top of the page about something the Hub could not read — its list of model packages, say — used to sit there until you pressed Dismiss, even after it had been reading fine for hours. It now goes away by itself on the next refresh that has nothing to report. A note about a file of your own that the app had to set aside still waits for you to dismiss it, because that one is not going to fix itself.

### Technical notes

- **A note about a failed Hub read now clears itself when the read answers again.** `refresh()` assigned `startupNote` only when the refresh had notes of its own, so a sentence put up by one failed read stayed on the banner until somebody pressed Dismiss — hours after the read had been answering fine. It is the same latch that kept *"A studio did not answer"* on every page, and it applied to every note a refresh raises (the package list, an unplaceable batch, an unreadable finished job). The assignment is now unconditional: a refresh with nothing to report clears the note the last one left. `recoveryNote` — a local file the app had to set aside — is untouched by this: it is composed into every refresh's notes, so it still stays until it is dismissed, and `tests/native/api_main.swift` pins both halves (the package-list sentence appears and is then gone; the "set aside" sentence survives a successful refresh).
- **A Studio that stops answering is a sidebar line, not a page-wide banner — and only for a Studio this Mac actually serves.** `native/StudioAPI.swift` appended *"A studio did not answer; the rest of this page is up to date."* to `startupNotes` whenever `GET /studio/transcription/api/transcribe/availability` or `GET /studio/voice/api/generate/availability` returned nil, and `startupNote` was only replaced when a later refresh had notes of its own, so a single 502 left the banner on screen until Dismiss — this Mac logged exactly one in a whole evening of polling (owner, 2026-10-28: *"what is it and should we get rid of it... or can we have it like in a notification icon somewhere instead?"*). It also fired for Studios that were never going to answer: both the Voice and the Subtitle Studio are `enabled: false` in this Mac's `machine_flags.json`. The sentence is deleted. `noteStudioAvailability` counts consecutive misses per Studio — **three in a row**, because at the visible two-second poll one miss is a worker restarting — reads the Hub's own `GET /api/hub/work-policy` on a missed poll with a 5 s timeout, and raises nothing unless the machine is enabled and a Studio with that modality is listed **and** enabled, named by the Hub's own title. It reads "still starting" while nothing has answered since connect and the connection is under two minutes old, and "not answering" after that; the first answered poll clears it. `StudioTroubleNote` draws it in the sidebar footer beside "Local services ready", with the detail on hover and on click. Proof: `tests/native/api_main.swift` pins a one-poll blip, a switched-off Studio, a paused Mac, a persistent failure that names the Studio in the footer and not in the banner, and recovery; `tests/native/view_checks.swift` pins that the line draws the Studio's own name (two notes differing only in the name must not render identically) and that no trouble draws nothing.

## 0.6.207 — 2026-10-28

### A render takes its place in the queue you set

Rendering used to jump ahead of everything else on a Mac that renders, whatever order you had arranged. A render is now one entry in that Mac's own work order, so the order you set is the order that runs — put it where you want it. A Mac that has never saved an order keeps exactly the order it has today, so nothing changes there by itself. Each Hub also reports how fast it renders, so the Gateway can send renders to the fastest Mac first.

### Every kind of work has its own switch

Each row of the work order now has an on/off switch, on by default. Switch a kind off and the Mac keeps it in the list but stops taking that kind of work; the Studio's own on/off switch works as before. A Mac whose settings were saved before this version reads as "on" for every kind, which is what it was doing.

### Chat can read a picture

The last version brought the part of Chat that reads an image, but the helper library it needs was not in the runtime the app ships, so a picture-capable model could only answer text. The runtime now carries it, and a build whose runtime lacks it fails instead of quietly shipping. You can send a picture with your question as before.

### Technical notes

- **Final render is ranked like every other kind of work.** Owner decision, 2026-10-28. The broker no longer puts a waiting render ahead of everything on a render Mac; render is one entry in each Mac's saved work order, so the order the owner chose is the order that runs. A Mac that never saved an order gets the one default (`vocabulary.DEFAULT_PRIORITY_ORDER`), which keeps today's behaviour. Each Hub reports its measured render speed so the Gateway can send renders to the fastest Mac first. `render_while_paused` is a separate opt-in, off by default.
- **Every kind of work has its own on/off switch, on by default.** Stored per Mac next to the work order (absent or malformed reads as on; only a real `false` is off). A kind switched off stays in the list with its switch, and the broker, arbitration and leases skip it. The native work-order page shows a toggle on each row. Per-Studio switches are unchanged.
- **Chat reads images with a multimodal checkpoint.** `mlx-vlm` 0.6.3 is now in the Voice runtime lock (16 packages added, no existing pin moved; `refresh-lock.sh --keep-locked-pins`), and `smoke_imports.py` fails a runtime that lacks it. In 0.6.206 a vision checkpoint answered text and refused images.

## 0.6.206 — 2026-10-28

### Setting up a Mac is a choice, on a screen, with the numbers in front of you

A new Mac used to get whatever the SSD kit decided to copy. Now the app has a **Set up this Mac** page that tells you what the Mac has before it asks you anything: its memory, how much space is free on the drive the models live on, and what each choice will copy and what will be left afterwards.

There are four starting points, one for each size of Mac:

- **Essentials 8 GB** — write, draw, narrate, transcribe, read pictures, search meaning, score.
- **Creator 16 GB** — adds the larger image model, the writing model, a better captioner and a second voice.
- **Creator Plus 24 GB** — adds music generation and Gemma 3 12B for longer writing.
- **Studio Pro 32 GB+** — adds the largest writing models, Qwen3.6 35B and Gemma 4 26B.

A preset reads "9 of 14" rather than "done", and only says installed when every file has been checked. Any model you add by hand sits alongside it.

**A model this Mac cannot run is shown, greyed out, with its floor and your Mac's memory** — never hidden, never quietly swapped for something else. Nothing is refused by name any more. Memory is the only thing that decides.

**The SSD kit no longer chooses.** "2 - Copy the models" now prints what the drive holds and points you at the screen. If you have a Mac with no internet and nothing else to copy from, "2 - Copy the models (everything)" still does the old job.

### Remove a model, and get the space back

The Remove button on the Models page used to refuse every time, so the only way to reclaim 9 GB was Finder — which deletes without a record and without checking whether a job was reading the file.

Remove now **moves** the model into the library's `_trash` with a note saying what it was and when, and takes it out of the app's list of installed models in the same step. It refuses, and says which, while a job is reading that model, while a paused model still holds it in memory, or while another installed model needs it. **Undo** is on the same page: a removed model is listed as recoverable and can be put back. Thirty days later it goes to the Mac's own Trash, where it is still recoverable.

### A one-time tidy-up for Macs that are already set up

If a Mac was set up before this screen existed, it carries whatever the kit copied at the time. **Set up this Mac** now offers to show you exactly which of those models sit outside that Mac's preset and how much space moving them would free — a dry run that changes nothing. Nothing moves until you approve it, the list you approve is the list that gets carried out, a model in use stays where it is and is named, and it only ever runs once.

### Chat can read a picture

The Chat worker loaded every model as a text model, which silently threw away the vision half of a checkpoint that has one. So the fleet's Gemma 4 26B-A4B QAT MLX 4-bit answered as a text model, and nothing could send it an image.

Chat now recognises a checkpoint that carries a vision encoder — from the checkpoint's own files, not from a list — and accepts images alongside text. The model list publishes that a model can read pictures, so the Gateway knows which Mac can do what.

**One thing to know about this build:** the part of the app that loads a picture-capable checkpoint is here, but the Python library it needs (mlx-vlm) is not yet in the helper runtime this release ships. On this build, Chat on a picture-capable model still answers text perfectly well and will tell you plainly that it cannot read the image here yet, rather than failing part-way through. The next release that rebuilds that runtime turns it on.

A picture also now counts for what it actually costs to read: a long prompt that fits on paper but not in the model's memory is caught before anything is generated, instead of being accepted and then truncated.

### See which Macs are on a slow cable

A Mac on a failing cable does not go offline — it **negotiates** a slower link and answers every other question as healthy, while its work crawls. Mac 1301 was linked at 100baseTX on an old cable and nothing in the fleet view could show it.

Every Mac now reports its connection: which interface carries its traffic, whether it is Ethernet or Wi-Fi, and, for Ethernet, the speed and duplex the machine says it negotiated. Wi-Fi reports its rate where the Mac states one. A reading that cannot be made says so, with the reason, rather than showing 0 — a Mac with no cable and a Mac that could not be read are different answers and now look different. The reading is cached and refreshed in the background, so looking at it never slows the Hub down.

### Fixes

- A network-link reading that died part-way left the Hub believing a refresh was still in progress, so that Mac would report its link as unmeasured for ever, with no reason to show. It now clears correctly and starts the next read.

### Technical notes

- **A new Mac is set up by choosing a preset sized to its memory, and the kit stops choosing for it.** Owner decision, 2026-10-28. Four install presets live in `model-presets/*.json` as data — **Essentials 8 GB** (9 packages), **Creator 16 GB** (14), **Creator Plus 24 GB** (16), **Studio Pro 32 GB+** (18) — read by `components/model_presets.py`; a fifth preset is a JSON file, exactly as a new model is a manifest, so neither needs a code change. Each file lists its own additions **and** every package the smaller presets carry, so a reader never has to union four files to know what a Mac gets, and `tests/test_model_presets.py` pins that superset property so a file that drops a smaller preset's package fails loudly instead of quietly shipping a thinner 32 GB Mac. A companion is never listed (`requires_packages` already says `yue2-3b-mlx` is no use without `yue2-vae`); `install_set` resolves companions through the app's own reader, as the downloader does. A broken preset refuses the whole screen naming the file and field rather than silently changing what a new Mac installs.
- **`GET /api/hub/model-presets` is the install screen's whole answer, computed fresh.** `components/hub/backend/model_install_plan.py` returns this Mac's memory, the free space on the **library's** volume (not the data dir's, which `resources.host_stats` reports), the four presets with counts taken from `model_packages.preflight` receipts, and every model grouped by job with its bytes, its floor, whether this Mac can run it and the sources that actually exist — a LAN share, then a mounted SSD kit, then the pinned internet URLs. A preset reads "9 of 14" and is complete only when every package is `files_verified`, so a preset that copied nine of fourteen says nine, not done. `florence-2-large-ft` and `phonon-2-mlx` carry manifests with no weights on any 2026-10-28 drive, so their only source is the internet and they keep their preset incomplete — reported, not hidden.
- **Shelving is deleted; RAM is the only gate.** `components/shelved_models.py` (`SHELVED_MODELS`), `keep_all_models`, the Hub's start-up move-out, both download refusals by name, the kit's "Shelved, not copied" skip and its `NOT_STOCKED` list are **gone**. The seven packages that list held — `whisper-base`, `whisper-small`, `voxcpm2-bf16`, `fish-audio-s2-pro-bf16`, `chatterbox-4bit`, `chatterbox-8bit`, `s3tokenizerv2` — and `qwen-image-2-1-mlx-serve-4bit` are ordinary catalog entries: listed, tickable, in **no** preset, gated by their own manifest floor. A copy already installed on a Mac stays where it is and works. `components/retired_models.py` is untouched and is now the single list that moves an installed package out.
- **One home for a memory floor.** `PROVEN_SMALLER`, `CARRY_BELOW_TIER` and `memory_admission._FLEET_DEFAULTS` each carried a number their manifests now state, so the catalog row lost to a table nobody could see was overriding it. FLUX.2 Klein 4B MLX 4-bit and Breeze TTS 2 MLX 4-bit move 16 → 8 GB in `model-manifests/` with their evidence (Klein stays `unqualified`: its 8 GB is a production-use floor with no stored peak receipt), the three tables are gone or empty, and `packaging/capture_model_manifests.py` follows.
- **The native "Set up this Mac" page is the choice.** `StudioPage.setup` (`native/StudioViews.swift` `setupWorkspace`), leading the Library section. This Mac's memory and the free space on the models' drive come first, then four preset cards with "9 of 14" / "Installed" and "copies X · Y free after", then every other model by job with size, floor, purpose and sources. A model too big for this Mac is **shown greyed out with its floor and this Mac's memory** — never hidden, never swapped. One request per package to install; there is no batch endpoint. The view names no preset and no package, so a fifth preset or a changed list needs no build of the app. `native/ShellCore.swift` adds the page; `bash native/build.sh` plus the boundary, API, secret-masking, view and lifecycle checks pass.
- **The kit stops choosing.** `2 - Copy the models.command` copies nothing by default: it prints what the drive holds and points at the screen. `plan_prune` is not even called, so a Mac set up from the screen is never reorganised by a kit run. `STUDIOHUB_KIT_COPY=1` — the new `2 - Copy the models (everything).command` — restores the old path for the one case it is for: a Mac with no internet and no other Mac to copy from. Lite Mode's own install set is retired; its runtime allowlist (`components/workstation_models.py`, `peers.lite_mode_allows`) is unchanged and the 8 GB preset carries every model on it.
- **Remove a model really removes it.** The Models page's Remove was a stub ("unavailable until it can protect models with active readers"), so the only way to get 9 GB back was Finder — which deletes without a receipt and without knowing whether a job was reading the file. `DELETE /api/hub/model-packages/{id}` (`components/hub/backend/model_removal.py`) **moves** the package folder to `<library>/_trash/<package>-<stamp>/` with a `.studiohub-removed.json` receipt recording what it was, when and why, and drops the installed receipt in the same breath so the app does not go on reading a folder that is no longer there as "installed under a different manifest". It is refused, naming which, while a job is reading the model, while a paused model's Studio still holds the weights, while another installed package requires it, or when this Hub does not own the library (`library_ownership`). `GET /api/hub/model-removals` lists what is recoverable and `POST /api/hub/model-removals/restore` puts it back; 30 days later the existing cleanup pass hands it to macOS Trash through `output_storage._macos_trash` — one deleter, not a second one.
- **A one-time cleanup for Macs that are already set up (owner addition).** `components/hub/backend/preset_cleanup.py` reports, per Mac, exactly which installed models sit outside that Mac's preset and how many bytes moving them frees — a **dry run that moves and writes nothing** (`GET /api/hub/preset-cleanup`). An approved `POST` calls the same `plan` and moves only on that answer, so the report the owner approves and the work that happens are one computation rather than two that can disagree. A model with an active job, one a lane is serving, or one another kept package requires stays put and is reported by name. `<data dir>/preset_cleanup.json` records that it ran, so a second apply is a no-op that says so and nothing re-runs it by itself. The target preset is the owner's choice, else the starter for the Mac's tier — **never derived from what the Mac happens to hold**, which would make the target move under the diff.
- **Chat reads a picture — the code is here, the loader is not in this build.** The Chat worker loaded every model with mlx-lm, whose gemma4 loader drops the vision tower, and its route refused any message whose content was not a string — so the fleet's **Gemma 4 26B-A4B QAT MLX 4-bit** checkpoint, which ships a vision encoder in its own `config.json`, answered as a text model and nothing could send it an image. The engine now reads the checkpoint's own `vision_config` and loads such a model with `mlx-vlm`; the completion route accepts OpenAI-shaped content parts (text plus `image_url` data URIs) and renders them through the model's own chat template; the Hub's pack normaliser passes them through instead of flattening them; and the catalog row publishes `supports_vision` and a `vision` capability, which is what the Gateway relays. A checkpoint is judged by **its own file**, not a curated list, so the next multimodal package works without an edit here.
- **A picture costs its own soft tokens, not one placeholder.** The rendered prompt carries one `<|image|>` per picture, which tokenizes to a single token; the processor then expands it to `vision_soft_tokens_per_image` (280 on Gemma 4). The context check counted the placeholder, so a 640 px scene plus a long prompt could be accepted and then refused by the model — or worse, silently truncated. The count now adds the checkpoint's own stated expansion per image and returns `None` (unmeasured) when the checkpoint states none, rather than pretending the placeholder is the whole cost.
- **A Chat runtime that predates mlx-vlm still serves text.** `mlx-vlm==0.6.3` is declared in `packaging/voice-runtime/requirements-pilot.txt` — the last release accepting the mlx 0.31.2 / transformers 5.14.1 that runtime pins — but a runtime without it would otherwise take every Mac's chat down with it, because the only fleet chat checkpoint is multimodal. Such a runtime loads the language tower with mlx-lm, sets `vision_unavailable_reason`, and refuses an image with a sentence naming the missing loader. **The shipped 0.6.206 Voice runtime is not rebuilt from that line** (see the handoff): `requirements.lock.txt` and `runtimes/voice` are unchanged, digest-identical to 0.6.205, so this release's Chat lane answers text and says why it cannot read an image. An image request is refused by name rather than failing inside a load.
- **Every Mac reports its network link, so a thin cable is visible.** Owner, 2026-10-28: Mac 1301 was linked at 100baseTX on an old cable (10baseT before that) and nothing in the fleet view could show it — a Mac on a thin link answers every other field as healthy while its work crawls. `components/hub/backend/network_link.py` reads this Mac's primary connection: the interface the kernel says carries the default route, the kind from the hardware port `networksetup` names for that device (`en0` is Wi-Fi on one Mac and Ethernet on another, so the device name is never the test), and for Ethernet the media that port says it negotiated (`1000baseT <full-duplex>` → 1000/full, `100baseTX`, `10baseT/UTP <half-duplex>`). Wi-Fi is reported as such with its PHY rate when `system_profiler` states one, plus whether Wi-Fi is also up beside the primary link.
- **The link reading guesses nothing and is never a zero.** `autoselect` with no cable, or a port forcing a media nothing answers, states no speed; an unreadable reading is `unmeasured` **with its reason**, never 0 Mb/s — "none" and "could not tell" must never be the same value. Every subprocess is bounded (5 s, and 20 s for `system_profiler`), runs as this user, and changes nothing. It is cheap because `/api/hub/resources` is on every controller's poll for every node: a request only ever reads the last answer, a daemon thread re-reads every 3 minutes, and the Wi-Fi rate on its own 10-minute timer. `resources.host_stats()` publishes it as `connection`, and it rides the host snapshot a node answers with as well as every `/api/hub/fleet/overview` row, so a Mac behind one-way node sharing — the one Mac nobody can dial — still reports its link on the check-in. An older Hub publishes no key at all, which a reader must treat as unknown.
- **A link read that dies never leaves the refresh flag set.** `_refresh` cleared the in-flight flag on its way out of an `Exception`, but a read that ended in anything else (a signal, a thread being torn down) left it set — and a set flag means `status()` never starts another read, so that Mac would answer `unmeasured` for ever with no reason to show. The flag is now cleared in a `finally`.

## 0.6.205 — 2026-10-27

### More models available on capable Macs

Every model can now be installed based on how much memory the Mac has, rather than being blocked by name.

Qwen 3.6 35B and Nemotron-Lightning are now available for 24 GB+ Macs (and copyable during kit setup), and Gemma 3 12B Instruct is added as an installable Chat model for 16 GB+ Macs.

### Technical notes

- **Models are governed by RAM, never refused or blocked by name.** Owner decision, 2026-10-27: *"They are models we can run so we should have them installable. Able to run on the machine or not is based off its RAM. We no longer block or lock any model."* Name-based blocks and pending-qualification locks are removed across the catalog and kit setup:
  - `qwen36-35b-4bit` and `nemotron-lightning-4bit` no longer declare `production_allowed: false`. That flag had placed them in `production_blocked` / `ineligible` on every Mac and kept them off the kit copy step ("not yet qualified; retained on SSD"). Both now publish a provisional floor (24 GB minimum, 32 GB recommended) labelled `PROVISIONAL, NOT MEASURED` in `memory_evidence`, derived from shipped weight bytes (19.03 GiB and 16.57 GiB).
  - `gemma-3-12b-it-4bit` leaves `RETIRED_MODELS` (where it was parked on 2026-10-02 as LTX's text encoder; the LTX video product stays retired) and is admitted to the catalog as a chat-mlx package (`model-manifests/gemma-3-12b-it-4bit.json`) for `mlx-community/gemma-3-12b-it-4bit` at immutable revision `86cc6a8dedbc456dd0e4af01a9d09f396f77e558` (13 runtime files, 8,068,018,787 bytes) with a provisional 16 GB minimum / 24 GB recommended floor.
  - The model catalog contains 40 packages total: 39 copyable production-allowed packages and 1 retired video model (`ltx-2-3-22b-ram-12gb-mlx`). Kit READMEs, SSD setup checklists, and document tests reflect that all 39 production packages are copyable according to each Mac's physical RAM.

## 0.6.204 — 2026-10-27

### A Mac that has fallen far behind can still update

An update can arrive as a list of the files that changed instead of a whole installer. There is a rule that gives up on that kind of update when the changed files add up to more than half the size of the full installer, and fetches the full one instead. Fine where a full installer exists — but a list of changed files leaves none anywhere, so for those the rule could only end the update for good. A Mac that had fallen far enough behind that more than half of the app had changed was refused on every check, and all the app could say was that the update could not be built.

That rule now applies only where a full installer really exists to download. A Mac far behind builds the update from the files it was sent, and a Mac only a little behind is unaffected either way.

### Technical notes

- **A files-only release is no longer refused for being bigger than half of a disk image that does not exist.** `release_sync._run_stage` handed `descriptor.get("bytes")` to the delta stage as `artifact_bytes` on every stage, so `release_delta.stage` refused any change set over `MAX_DELTA_FRACTION` (0.5) of that image with `over_threshold` — including on a controller whose release arrived as a manifest, where `store_release` had already deleted the image, so the refusal's only answer (download the image) was a guaranteed 404. On 0.6.201 that cost a rollout cycle; on 0.6.203, which stopped treating the image as an option for a manifest push, it is a hard stop: a Mac that had fallen far enough behind for more than half its app to differ was refused on every check and stranded. `release_sync._delta_cap_bytes` now returns the descriptor's `bytes` only where an image can actually be installed — this Mac's own held image, or, on a node, a controller whose own `/api/hub/release` says it holds one (`delta.artifact`, recorded against the release it mirrored): a node's only fallback is a relay to its controller, and a controller that took the release as files has none to relay. Where no image exists the stage builds, which is what a file manifest is for. `tests/test_release_delta.py` remains the contract for the cap itself; `tests/hub_regression/cases/test_release_delta_routes.py` gains three cases — a controller and a node ending `staged` where the changed bytes exceed the cap, and a release that really has an image still ending `fallback` / `over_threshold` so the cap is not merely absent. Both `staged` cases end `fallback` / `over_threshold` against 0.6.203's line and `staged` here.

## 0.6.203 — 2026-10-27

### Updates install again on a slow Mac

A Mac on a slow connection could get stuck: the app waited a while for the changed files, gave up, and went looking for the full disk image — but a release that arrives as a list of changed files has no disk image anywhere, so that download could only fail. It now waits for the files, and if they are not ready yet it simply tries again on the next check. Nothing is left half-installed and nothing reports a failure that is not one.

If this Mac really cannot build the update from the changed files, it says why, in plain words, and stops — it no longer goes hunting for a file that was never made.

### Rolling out to the whole fleet at once

Releasing to every site used to stop at the first Mac that was busy or asleep. Now each site is handled on its own: a busy site is retried on its own, an unreachable one is set aside and retried later, and a Mac that stays quiet is a note in the report rather than the end of the rollout. One site can no longer hold up the rest.

What has not changed: nothing is pushed to a site while it is working, and the release file is checked byte for byte and opened read-only before anything is published.

### KH Updater: installing it now also starts it

The KH Updater installer used to install the agent and leave you to start it. It now starts it for you, and leaves an agent that is already running alone. If the start fails, the install is **kept** — you are not made to enrol the Mac a second time — and it prints the one command that retries it. The installer on the SSD kit behaves the same way.

### Technical notes

- **A files-only release never looks for a disk image that does not exist.** At site 1100 a member Mac's stage ran long, the app abandoned it and downloaded the disk image, and the relay answered `HTTP 404. Nothing was installed.` It recovered only when a fresh app process found the stage done, after the rollout driver had halted. A manifest push carries `files_sha256` and leaves no disk image anywhere in the chain, so the fallback could only ever answer 404. Four changes, one contract: `GET /api/hub/release` publishes `delta.artifact` (whether an image can be installed for the offered release) and `readRelease` reads it, with a Hub that predates the field judged by the signed descriptor — the same fact; the app stages a release that carries a file manifest and never falls back from it, so a stage that is still running, unreadable or past its own deadline ends `not_ready` (retried on the next check, not a failure) and a Hub refusal ends with the Hub's own reason; `release_sync` gives a stage **one** deadline (`BLOB_WAIT_S`) for the whole run instead of one per missing file, so no stage can outlive the app watching it. The image path stays for releases that really have one. Proof: the new native cases fail on 0.6.202 with "1 image" installed for a files-only release, a refused route and a transient read error; the shared-deadline case ends `staged` on 0.6.202 and `not_ready` here.
- **A fleet rollout fans out to every site at once.** `scripts/hub_rollout.py` (owner, 2026-10-26: *"it shouldn't block the rest. we can just keep updating or installing ... we can just fan it out."*) supersedes the paced one-site-at-a-time driver, which halted every site on the first busy, unreachable or silent Mac. Acceptance is from the bytes on disk (size and SHA-256 recomputed, `hdiutil verify` VALID, mounted read-only at a private temp mountpoint — never under `/Volumes`, so a kit SSD is never touched). One worker per site; a site is publishable only when it is reachable, its own `running_jobs` is empty and the Gateway has no non-terminal job routed to it, with `queue_depth: None` and nothing running recorded as *queue unknown* rather than a zero. A busy site is re-polled inside a bounded 2 h window, an unreachable one is *deferred: unreachable* and retried in the same window, and a silent member is a warning in the receipt — none of them halts another site. One accepted POST per site, a receipt opened `x` and never reused for another artifact, no republish on a rerun, and this Mac's `local_paused_at` is read and recorded but never written. `scripts/test_hub_rollout.py` is 31 tests against fake Gateway responses with no network, including the read-only mount and its detach exercised through a fake `hdiutil`.
- **KH Updater agent 0.1.1: installing the agent also starts it.** kh-updater `feat/kit-activates-20261026` makes `packaging/install-agent.sh` load the LaunchAgent after a completed install, with the same rule as `khu_agent.Bootstrap.activate`: probe `launchctl print gui/$(id -u)/com.khupdater.agent` and bootstrap only when that job is not loaded, so an already-loaded agent is left alone and never doubled. A failed start keeps the finished install, prints launchctl's own reason and the one command that retries it, and exits non-zero; the staging rollback now stops at the committed install. The kit rebuilt from that commit is `0.1.1`, staged into this release's `components.hub.khu_agent` (the binaries are byte-identical to 0.1.0's; the installer is what moved).
- **The SSD kit's installer follows the same rule.** `packaging/kit-khu/khu-install.py` used to raise on a failed `activate()` and move the whole finished install — the enrolled config and the launchd job — into `~/Library/_trash`, so a launchd hiccup cost the person a second enrolment. `Bootstrap.activate()` now raises `StartFailed` carrying launchctl's stderr and the plist path; the run prints it and `launchctl bootstrap gui/<uid> <plist>`, keeps the install and exits 1. A job launchd already has loaded is left alone and the run says so. A refused enrolment still trashes the partial install, because there is nothing worth keeping. `packaging/kit-khu/Install KH Updater.command` and `packaging/kit-readmes/KH Updater.md` say the same thing.
- **Rules refresh, and the retired video names are gone from prose.** `docs/tasks/HANDOFF.md` is START HERE (BOARD.md is historical as of 0.6.194); the release scope is `HUB_RELEASE_ARTIFACTS`; only edits to the **same** shared files are serialised, independent files run concurrently; `.build/` scratch has one narrow carve-out (`packaging/prune-build-scratch.sh`). `tests/test_video_retired.py` is rewritten off `RETIRED_MODELS` itself, so every id it tests comes from the retirement list and the file cannot keep passing after the list changes; `packaging/hub_worker.py` names the retired manifest once (`VIDEO_MODEL_PACKAGE`) and `tests/test_chat_packaging.py` derives its fixture from it. The retirement entries and `components/video/` stay: the list is how an installed copy is moved to the Trash Folder and refused by name, and the worker is the documented restore path.
- **The per-release gate runner is in the repo.** `scripts/hub_offline_gate.py` (`--root` / `--evidence`, refusing an existing evidence directory) is the accepted 0.6.201/0.6.202 runner with only the root, the evidence dir and the interpreters parameterised, so a release no longer copies a per-version `.build/hub-offline-gate-<n>.py`.

## 0.6.202 — 2026-10-26

### Reference to Image: make a picture from your own photos

Image can now take one or two reference pictures and make a new image from them. Pick **FLUX.2 Klein 4B MLX 4-bit** or **Qwen Image 2.1 Turbo Low-RAM MLX 4-bit**, add your references in the order you want them used, and write the prompt as usual. Both names include the quantization, because that is what runs.

**Which Macs can do it:**

- **16 GB and above** run FLUX.2 Klein 4B MLX 4-bit edit. A 32 GB Mac also gets its full-speed setting.
- **32 GB** is needed for Qwen Image 2.1 Turbo Low-RAM MLX 4-bit edit — two references at its lower-memory setting still need about 24 GB.
- **8 GB Macs are not offered Reference to Image.** Two references need about 7.5 GB of working room on top of everything else, so the app refuses with a plain message instead of starting and swapping.

**A smaller Mac is slower, but it does not swap.** The app looks at this Mac's memory and picks the lower-memory way of running the edit automatically: it holds one piece of the model at a time and decodes the picture in smaller pieces. That is slower on a tight Mac, and it is the reason nothing gets pushed into swap. If a Mac has no measured way to run the setting it would pick, it says so and refuses rather than starting a render it cannot hold. The full-speed setting is still there for a Mac with room to spare.

The published numbers, measured through the worker: Klein edit takes about 98 s for one reference and 152 s for two at full speed, or about 69-106 s in the lower-memory setting — which was both smaller and quicker on the Mac we measured. Qwen edit takes about 155-192 s at full speed and 162-183 s in the lower-memory setting.

### The Image engine is updated

The Image engine moves to mflux 0.23.0, with MLX 0.32.4 and Pillow 12.3.0 (Pillow 13 does not run on the Python the app ships). The ordinary text-to-image models were re-measured on it and did not slow down: Klein 31.6 s, Z-Image-Turbo 85.9 s, Qwen Turbo 93.5 s. The older Qwen workaround for renaming weights is gone, because this version of mflux loads the pack itself.

### A square reference request stays square

A bug dropped the aspect ratio on reference requests, so a 1:1 request quietly came back 16:9. It now uses the shape you chose.

### Technical notes

- **Reference to Image: FLUX.2 Klein 4B MLX 4-bit and Qwen Image 2.1 Turbo Low-RAM MLX 4-bit edit an image from one or two references.** `POST /api/generate/edit` is multipart with one `images` part per reference plus the lane's ordinary fields; the worker validates count, readability and media type, stages the uploads under `<data>/inputs/<job id>/`, deletes them at terminal, and records `mode: "edit"`, `reference_count` and `reference_digests`. The broker resolves every reference in order, refuses a count outside the worker's published window, refuses a worker that does not advertise `edit`, and republishes `reference_count` in `terminal_result`. Each row carries a second class (`edit_model_class`) rather than a second package — `Flux2KleinEdit`, and `QwenTurboEdit` over mflux's `QwenImage21Edit` — so the qualified text-to-image lane is unchanged and both lanes read the one installed q4 pack. The published window is 1-2 references at up to 1024 px: what was measured, not mflux's own cap of 10.
- **Each edit lane has its own measured memory tiers.** `ImageModel.reference_memory` is the lane's own `MemoryPolicy`; `memory_plan(lane="edit")` reads it, `plan_configuration()` names the result (`default` / `bounded-staged` / `bounded-streamed`), every edit job records the configuration it ran as `reference_mode`, and `/api/catalog` publishes the lane's answer in `loading_modes.edit`. `loading_mode` `auto` / `low` / `full` applies to edit exactly as to text, with no forcing rule. A Mac whose chosen configuration has no measured floor refuses the lane (and says so) instead of starting a render it cannot hold.
- **Measured through the worker on mflux 0.23.0** (32 GiB M2 Max, 4 steps, 1024 canvas, references at 1024 px; `qualification/evidence/image-edit-2026-10-26.jsonl`). Klein: `default` 98.03 s / 151.56 s at a 12.01-12.02 GB MLX peak; `bounded-staged` 72.39 s / 101.24 s at 5.69-5.76 GB; `bounded-streamed` 69.13 s / 105.56 s at 5.69 / 7.47 GB — bounded was both smaller and faster on this host. Qwen: `default` 154.51 s / 191.82 s at a 24.2 GB peak with 9.5 GB of swap growth at two references; `bounded-staged` 162.26 s / 182.96 s at 10.2-12.8 GB with none. Published floors: Klein `default` 32.0 GB total / 14.0 GB free (the only host measured), `bounded-staged` 12.0/8.0, `bounded-streamed` 12.0/9.5 — two references need 7.47 GB of working set, so an 8 GB Mac is refused and the real minimum is 12 GB. Qwen's unbounded configuration publishes **no** floor (a Mac that would select it is refused until a larger host is measured), and its bounded one needs 24.0 GB total / 14.8 GB free, which the 16 GB tier does not clear.
- **The Image runtime moves to mflux 0.23.0.** `packaging/image-runtime/requirements.in` 0.20.0 → `mflux==0.23.0`, plus `urllib3>=2.8.0` (0.23.0 requires it; the previous lock pinned 2.7.0) and `pillow==12.3.0` (13.0.0 imports a symbol that exists only on Python 3.14 and takes mflux down on the 3.12 runtime). Relocked with `uv pip compile --generate-hashes`, `constraints-sonoma.txt` re-pinned to `mlx` / `mlx-metal` 0.32.4 (the version every edit measurement ran on), and `build.sh`'s embedded lock digest updated. The relock that shipped also adds `python-multipart==0.0.32` (see below); nothing else in the lock moved. mflux 0.23.0 also removed `Qwen21Initializer._load_weights`, so the 0.22-era Qwen weight-renaming adapter is gone — 0.23 loads this pack itself. Regression proof on 0.23.0 (`qualification/evidence/image-runtime-0.23-2026-10-26.jsonl`): Klein txt2img 31.6 s / 9.79 GB, Z-Image-Turbo 85.9 s / 10.02 GB, Qwen Turbo 93.5 s / 5.91 GB.
- **`aspect_ratio` no longer disappears on a reference request.** `broker._multipart_fields` passes `aspect_ratio` through, and now `resolution` too; before it dropped the field, so a 1:1 reference job silently ran at the model's 16:9 default.
- **The Qwen Image 2.1 mflux pack's licence record says `qwen-research`.** `packaging/capture_model_manifests.py` and `model-manifests/qwen-image-2-1-mflux-q4.json` recorded the conversion's `Apache-2.0` rather than the weights' licence. The licence is not part of `manifest_digest`, so installed receipts stay valid.
- **The packaged Image worker could not start, because its runtime had no `python-multipart`.** The Reference-to-Image route declares FastAPI `Form` / `File` parameters, and FastAPI raises during route registration when python-multipart is missing — so `create_app` failed inside the packaged worker and the Image lane never came up. Every gate passed because the test environment already had the package; the shipped runtime did not. `packaging/image-runtime/requirements.in` now pins `python-multipart==0.0.32` (the version the tested code path used), the lock carries it and nothing else moved, and the runtime was rebuilt. Found by starting the packaged worker and posting a real edit, which is the step the gate cannot do.
- **The Image runtime pin moves with the runtime.** `packaging/stage_voice.py`'s `IMAGE_RUNTIME_SHA256` is now `b720c5bc…`, the runtime built from the 0.23.0 lock, and the mflux 0.20.0 tree (`c5ac478f…`) is named in `IMAGE_RUNTIME_SUPERSEDED` so a build handed the old one says which it is instead of only refusing.
- Gates on this source: offline unittest 3125 run / 15 skipped / 0 failures / 0 errors (exit 0; 0.6.201 was 3084 — the 41 new cases are the reference lane, the edit routes, the broker reference handling, the tier guard and the two that keep `python-multipart` in the runtime); hub regressions 439 passed (exit 0); voice 1016 passed / 28 skipped (exit 0); native build and its API, secret-masking, view and lifecycle checks passed (exit 0).

## 0.6.201 — 2026-10-26

### A Renders page shows what this Mac rendered

Library now has a **Renders** page: how many renders finished, how long they took in total, and a newest-first list of each one with the machine that did it, the quality it was rendered at and why it failed if it did. Open a row to see its details and **play the video right there** while this Mac still holds it; if the file has already gone back to Story Studio, the page says so instead of showing a broken player.

A Mac on the network that does not answer is shown as not answering, so the page still works and you can see which Mac to look at. Renders that finished before this version were not recorded by the Hub, and the page says that rather than showing zero.

### A render can carry your channel and episode name

Story Studio can now send the channel and episode with a render, and the Renders page shows them instead of that long job number. It is a label only — it never decides where a render goes or whether it is accepted. A render with no name keeps its job number.

### The Voice runtime no longer carries its tests

Every Python library the Voice engine needs arrived with its own test files, and the previous build also carried the pytest test tool itself — about 41 MB of nothing the app ever runs. This build removes it, and the build now refuses to produce an installer if a runtime it could not clean is left in place. The proof that nothing that matters moved: of the 31,686 files left, 31,685 are byte-for-byte the same as the previous release, and the one that differs is the list of files the runtime keeps. Voice was tested on the cleaned runtime and passed in full.

### The SSD kit is checked in the test suite

The KH Updater folder on each kit drive, the README that appears when there is no publisher key, and the installer that enrols a Mac are now all pinned by tests, so a later change cannot quietly break kit setup or touch the signing-key backup folder.

### Technical notes

- **A Renders tab shows what this Hub rendered, how long it took and where the video is.** `components/hub/backend/render_history.py` reads `render_jobs` in `hub.db` into entries and totals; `components/hub/renders.py` publishes `GET /api/hub/renders` (this Mac, and on a controller every joined Mac), `GET /api/hub/renders/{id}` and the per-Mac `GET /api/hub/fleet/nodes/{id}/renders`. A Mac that does not answer is named as not answering, so it never fails the page and never reads as "that Mac rendered nothing"; `fleet_nodes.read_node` is a bounded read-only dial that never queues. A length, a wall time, an average and a speed are each a measured number or absent — never `0` for "the Hub does not know" — and `unknown_duration` counts the finished renders whose length could not be stated. Only a succeeded render contributes output minutes. `render_jobs` gains `started_at`, the moment the render process was bound, with the row's existing settlement time as the other end. Native: a Renders page in Library with totals, a newest-first list and a detail sheet that plays the video from disk while this Mac still holds it, and otherwise says it went back to Story Studio. Renders accepted before this build were never recorded, and the empty state says so.
- **A render can be named by its channel and episode.** Story Studio sends an optional, display-only `display: {channel, episode}` with a final-render request. The Hub accepts it in the closed request envelope, stores it (migrated column `display_json`, NULL for a row accepted before this), and publishes it on each Render-tab row. Nothing dispatches, routes, hashes or accepts on it, and a render with no name keeps being titled by its Hub job id.
- **The Voice runtime ships no test files, and staging refuses a tree it could not clean.** A Voice runtime is a whole Python installation, so every wheel's `tests/` directory travelled with it, and 0.6.200's bundle also carried pytest 9.1.1 with pluggy and iniconfig. `packaging/voice-runtime/runtime_packaging.py strip-tests` removes wheel test trees, `conftest.py` / `pytest.ini` / `tox.ini`, `test_*.py` / `*_test.py` and the test frameworks themselves, rewrites `manifest/runtime-files.json` so the runtime no longer describes files that are gone, and refuses a tree it could not clean rather than reporting success. `build.sh` runs it before `audit` and `manifest`, and `stage_voice.stage()` runs the same sweep on the copy, because `.build` trees are reused between releases. A name is not evidence: anything the remaining sources still import is kept — `mpmath/__init__.py` imports its `tests` package, `jinja2`'s `tests.py` is a runtime module, `scipy/stats/_page_trend_test.py` *is* `page_trend_test`, and `torch/_dynamo/trace_rules.py` imports `torch._inductor.test_operators` at run time; removing that one by filename broke `AutoTokenizer` and with it Whisper transcription. Proof on the shipped 0.6.200 runtime: 3,209 of 34,895 paths removed (41.0 MB), and 31,685 of the 31,686 that remain are byte-identical by sha256, size, mode and symlink target — the only difference is the rewritten manifest.
- **The SSD kit's KH Updater folder is pinned by tests.** `tests/test_khu_kit.py` covers the four-folder drive layout, the no-install-file README when the publisher key is absent, the environment-variable form of the same two inputs, the signing-key backup folder that a run leaves byte-identical and never names in a README or checksum, and the installer that enrols and starts the agent with the config the Hub itself writes. The kit's behaviour is unchanged from 0.6.199.
- Gates on this source: offline unittest 3084 run / 15 skipped / 0 failures / 0 errors (exit 0); hub regressions 435 passed (exit 0); voice 1016 passed / 28 skipped (exit 0); native build and its API, secret-masking, view and lifecycle checks passed (exit 0).

## 0.6.200 — 2026-10-26

### A corrected build of 0.6.199

Version 0.6.199 was published without the two large runtimes that make Image and sung-song Music work, so those features would not start on a Mac that installed it. This build has the same features as 0.6.199, made again with both runtimes included. If you installed 0.6.199, install this one.

### A missing runtime now stops the build

The build that makes the app now checks that the Image and Music runtimes and their worker files are actually inside it, and refuses to produce an installer when one is missing, naming what is absent. The previous build could finish "successfully" with them left out.

### It installs on a Mac still running the older version

The update check a Mac performs comes from the version it already has. Version 0.6.199 declared one of its pieces in a way the older check did not recognise, so it stopped before copying anything. This build declares it in the way the older check expects, and the build now checks that future releases stay compatible with the versions already installed.

### Technical notes

- **0.6.199's installer was missing the Image and YuE2 runtimes; this rebuild carries them.** 0.6.199's helper staged the app with only `--hub --khu-agent-kit --khu-publisher-json`, so `_stage_image` and Music's YuE2 branch both returned `None`: the DMG was 592,715,776 bytes against 0.6.198's 1,417,708,032, `Resources/runtimes` held only `voice`, and `image_worker.py` and `yue2_runner.py` were absent. The source is unchanged — only the packaging inputs were wrong. 0.6.200 has the same features as 0.6.199, staged again with the qualified Image (mflux 0.20) and YuE2 trees and the KH Updater agent kit.
- **The build now refuses a Hub release that drops a declared runtime or worker.** `HUB_RELEASE_ARTIFACTS` (`packaging/stage_voice.py`) declares the runtimes (`voice`, `image`, `music-yue2`) and worker entry points a Hub release ships. `stage()` checks the staged bundle before hashing anything, refuses when one is missing, names every missing artifact and the flag that stages it, and preserves the failed bundle under `_trash/failed-stages/`. `tests/test_voice_bundle.py` stages a voice-only Hub app and pins the refusal. The guard cannot catch a runtime with the wrong contents (the Image and YuE2 content pins do), a missing model package (models live outside the app), a file altered after staging (the release manifest and a mount comparison do), or a DMG step that drops something staging had (the mounted image must be proved).
- **0.6.200 installs on a Mac still running 0.6.198's installer.** The installer that validates a release is the one **already installed**, and 0.6.198's `ReleaseInstaller.runtimePaths` (commit 20670dd) has no `khu-agent`. 0.6.199's manifest listed a top-level `khu-agent` component, so every 0.6.198 Mac refused the update at manifest validation, before downloading a byte: *"lists component 'khu-agent' with runtime 'khu-agent', which this installer does not know"*. Adding the name to 0.6.200's own Swift cannot help — the old binary validates. The kit now rides inside the `hub` component's record (`components.hub.khu_agent`) and `Resources/khu-agent/` is unchanged; `khu-agent` is nevertheless registered in `runtimePaths` and `COMPONENT_WORKERS` so a later release can list it as a component once every installed installer can read it. `tests/native/fixtures/update-client-runtimes-0.6.198.json` pins the fleet installer's runtime set (re-derived from the 0.6.198 commit by `tests/test_installer_knows_shipped_runtimes.py`), `tests/native/fixtures/release-manifest-0.6.199-f7f9fab.json` is the refused manifest, and `tests/test_voice_bundle.py` proves a staged release passes the same rule.
- Gates on this source: offline unittest 3006 run / 15 skipped / 0 failures / 0 errors (exit 0); hub regressions 435 passed (exit 0); voice 1016 passed / 28 skipped (exit 0); native build and its API, secret-masking, view and lifecycle checks passed (exit 0).

## 0.6.199 — 2026-10-26

### The KH Updater agent is now active

The KH Updater agent built into Studio Hub now turns on automatically when the publisher config is present. If a Mac's token gets dropped by the publisher, the agent detects it (with a safe probe that does not refresh `lastSeen`), preserves the dead config as evidence, and enrols again. The status page shows which Macs needed re-enrolment.

### The SSD kit carries the KH Updater installer

Every drive prepared with the kit now includes a `KH Updater/` folder with the installer (`Install KH Updater.command`), the apps config, and the installer script. The folder appears when both the agent kit and the publisher config are passed to the kit builder; without both, the drive gets a README saying the publisher key has not been created yet.
### Fleet re-point without re-joining
A Mac can now switch to a different controller without being uninstalled and rejoined from scratch. The Settings → Fleet screen gains a **Leave** button that drops the controller credential and stops the check-in loop, and a **Reset** button that wipes the machine identity for a fresh start. The Machine ID field is now editable, so a renamed Mac re-enrols with the new name rather than carrying the old one.

### Technical notes

- **The KH Updater agent becomes active when the publisher config is present.** The bootstrap (`components/hub/backend/khu_agent.py`) now handles re-enrolment when the publisher drops a Mac's token: it probes with `GET /commands` (no `capabilities` parameter, so it never refreshes `lastSeen`), preserves the dead config as evidence, and enrols again through the window-gated flow. The status endpoint reports `tokenRejected: true` when this happens, so an operator can see which Macs needed re-enrolment without reading logs.
- **SSD kit carries the KH Updater installer for every Mac.** `packaging/kit-khu/` holds the installer (`Install KH Updater.command`), the apps config (`apps.json`), and the installer script (`khu-install.py`, 675 lines). `packaging/kit-restructure.sh` and `packaging/release_kit.py` lay out a `KH Updater/` folder on each drive when both `--agent-kit` and `--publisher-json` are passed; without both, the drive gets a README saying the publisher key has not been created and no install file at all. The kit-readmes gain `KH Updater.md` (two-step version for the publisher) and `KH Updater - not ready yet.md` (the placeholder when the publisher is not yet set up).
- **Fleet re-point without re-joining; leave/reset; editable Machine ID.** `POST /api/hub/fleet/leave` drops a Mac's controller credential and stops its check-in loop; `POST /api/hub/fleet/reset` wipes the machine identity so the next start is a fresh install. The Settings → Machine screen exposes an editable Machine ID field (`POST /api/hub/machine-identity`) that rewrites `machine_flags.json` and triggers a re-enrolment with the new name. `components/hub/backend/fleet_nodes.py` gains `leave()` and `reset()`; `components/hub/standalone.py` wires the routes and the native `MachineIdentityEdit` view.
- Gates on this source: offline unittest 3001 run / 36 skipped / 4 failures / 2 errors (release notes tests failed on this worktree, pre-existing timeouts in fleet death and fleet-pull tests, image bundle and model copy script errors under investigation); hub regressions pending; voice pending.

## 0.6.198 — 2026-10-25

### Remote final rendering takes real episode sizes

When a Mac finishes a final video for the fleet, it used to accept one picture size at 24 frames a
second. It now accepts any even size up to 4K and 24, 25, 30 or 60 frames a second, as long as the
whole job fits the Mac's work budget (about three hours of 4K at 60 frames a second). Everything
else about a remote render is unchanged, and remote rendering still stays off until a Mac is
switched on in the Gateway and a remote render has been shown to match a local one.

### The KH Updater agent is built in, but switched off for now

Studio Hub can now set up and run the KH Updater agent on a Mac. The support is built into the app
but does nothing yet: it turns on only once your publisher details are in place, and this build was
made without them, so it simply reports that it is waiting. Updating the app itself is still KH
Updater's job, not Studio Hub's.

### Technical notes

- **Remote final renders accept real episode sizes.** `execution_bounds` gains `bounds_version` 2 (components/hub/backend/render_profiles.py): one pinned canvas becomes a **range** — `canvas_range` `max_edge` 3840 with `max_pixels` 8294400, so any even canvas up to 4K — and `working_fps` becomes **[24, 25, 30, 60]** instead of 24 only. Work is now measured in pixel-frames (`width × height × ceil(duration × fps)`) against `max_pixel_frames` 5,374,771,200,000 (about 3 h at 4K60), replacing the fixed `max_pixel_frames` that pinned one size at one rate. Every other fence (assets, operations, bytes, wall seconds, memory, scratch) and `production_eligible: false` are unchanged, and the codec/container handling is untouched. A version-1 profile (`canvas_pairs`) still validates, so an older pinned candidate keeps loading. `packaging/render-authority/PROFILE.json` re-frozen (970 B, sha256 `ad3f1c9d47d5f594…`) and re-pinned in `packaging/stage_render_tools.py`; the Gateway re-freezes the identical bytes.
- **The KH Updater agent is built into the Hub, inactive until the owner's publisher is set up.** `components/hub/backend/khu_agent.py` is a per-user bootstrap started by `create_app`: it checks, installs, enrols and activates the agent from a staged kit, sends **no credential** on enrolment, and reports one honest state on `GET /api/hub/khu/status`. The kit travels inside the Hub **only when both** `--khu-agent-kit` and `--khu-publisher-json` are passed to `packaging/stage_voice.py` (`stage_khu_agent.py`); handed one without the other, or neither, the build says so on stderr and its manifest records no `khu-agent` component, so the loop reports it is waiting. KH Updater remains the authority for app updates.
- Gates on this source: offline unittest 2937 run / 15 skipped / 0 failures / 0 errors; hub regressions 435 passed; voice 1016 passed / 28 skipped.

## 0.6.197 — 2026-10-24

### Narration clips play again

Clips made through the Gateway are M4A files. The Hub only accepted WAV when checking a clip before
playing it, so it refused them with "Voice clip verification failed". Any real audio file now plays
and saves, with the right file extension.

### Each clip says which voice spoke it

Rows in Recent audio and Jobs show the voice's name and ID.

### A little room around the "working" ring

The coloured ring that shows work in progress now sits a few points away from the list it
surrounds, instead of touching it.

### Technical notes

- **Voice and music clips in M4A, MP3, FLAC or Ogg play and save.** `audioData`/`musicData` (native/StudioAPI.swift) verified the bytes and checksum and then required a RIFF/WAVE header, so every Gateway narration clip (the Gateway asks for M4A) failed with "Voice clip verification failed" although its bytes matched the receipt. Verification now requires any real audio container (`audioContainer(of:)`, nil for non-audio); Save names the file by the job's media type.
- **Audio rows say which voice spoke.** The stage route takes an optional `display_name` (a label: not part of the asset identity); the broker records `item["voice"] = {name, id}` from the staged reference (id = the sender's `source_asset_id`, else the asset id) before dispatch. A job using this Hub's own library voice shows that voice's name from the library. Shown as "voice Name · id" on the row. Gateway 0.2.197 sends the name.
- **The generating ring keeps a margin.** `ActiveAura(inset:)` pads what it surrounds by `HubSpace.s` (always, so nothing moves when work starts); used on the Recent audio list and the Image and Music previews.

## 0.6.196 — 2026-10-24

### Remote rendering is switched on from the Gateway

A Mac takes final renders for other Macs only after you switch it on in the Gateway (Fleet →
Remote rendering). It stays off until you do.

### Each Mac can be given its own image model

When the Gateway's image pool is used, each Mac makes images with the model for its memory size,
or the one you chose for it in the Gateway. Jobs that ask for a particular model by name still run
on any Mac that has it.

### Opening a folder in Finder no longer upsets the Hub

The hidden files Finder leaves behind (such as `.DS_Store`) are now ignored everywhere the Hub
checks its own folders.

### Technical notes

- **Remote final rendering can be switched on per Mac from the Gateway.** `POST /api/hub/fleet/nodes/{id}/render-authority` (Fleet token; not in `NODE_ALLOWED`) relays the owner's choice to the Mac's own `POST /api/hub/render-authority`, stored at `<data root>/render-authority/opt-in/unqualified.json`. Off by default, and then byte-identical to 0.6.195; while on, the leaf publishes `accepted_unqualified` on its render capability row.
- **Per-Mac image-pool model.** `registry.assigned_image_model` / `set_assigned_image_model` in `machine_flags.json`; `POST /api/hub/fleet/nodes/{id}/assigned-image-model` (Fleet token); new routing form `assigned` — the broker (`_can_run`) runs such work only on a Mac whose assigned model is the job's model. Published as `assigned_image_model` on every Mac row of the fleet overview. Work with any other routing ignores it.
- **macOS metadata never makes the Hub refuse its own folders.** `components/os_metadata.py` (shared names: `.DS_Store`, AppleDouble `._*`, `.localized`, `Icon\r`, Spotlight/fsevents/Trashes folders) is skipped by the render asset store scans, the file manifest and model-catalogue discovery; a genuinely unknown entry is still refused.
- Gates on this source: offline unittest 2901 run / 15 skipped / 0 failures / 0 errors; hub regressions 435 passed; voice 1016 passed / 28 skipped.

## 0.6.195 — 2026-10-21

### A Mac on your fleet can now finish a final video

When the Gateway has a finished set of video pieces for a final render, Studio Hub can take that
job and hand back the finished video. It does the final pass on the Mac — overlay layers, colour
grade, vignette, film grain, the aged-film look, letterbox, presentation frame and logo — using
the render tools that ship with the app. It loads no model, so it does not take time from Image,
Voice or Chat work. A Mac finishes one render at a time, and the work waits its turn under the
pause, Lite Mode and maintenance settings you already use.

### The finished file is returned in verified pieces

The Hub writes the job down before it starts, so a request that arrives twice still runs once, and
cancelling stops that exact render. If the app restarts and cannot prove what happened to a render,
it says so instead of quietly starting it again. The finished video is handed back only in checked
byte ranges tied to the exact output the Hub measured, and the job is not cleared until the other
side acknowledges it.

### It stays off until you allow it

A Mac does not become a renderer just by updating. It needs a record placed for its exact hardware
class; until then it advertises no render capacity. A separate, one-use qualification run can be
allowed without turning production on. That permission can be used once, for one exact request, and
only a finished qualification run can produce the measured record that turns production on.

### Only the video settings that Mac can really produce are offered

The Hub works out what a Mac can actually encode from the same renderer that will run the job, and
offers only those settings. A request for an encode the Mac cannot produce is refused before any of
its files are kept or any work is started, so it cannot fail halfway through. A qualification run's
one-use permission is treated the same way, so the first test render is matched to what that Mac was
measured for.

### Nothing is deleted when a render is cleaned up

Finished and abandoned render files are moved into your Trash folder after a quiet period, never
deleted. Anything still in use, still being downloaded, still waiting to be published or still
needed to finish a job is left alone, and a finished file is only moved once the other side has
confirmed it received it. If the Trash folder is not available, the files stay where they are
instead of being moved somewhere unexpected.

**This build is a candidate.** It has not been qualified on hardware, no render has been tested
end to end with the Gateway, and it is not part of a released or installed build. No qualification
record has been produced.

### Technical notes

Prepared on `feat/fleet-render-finish-20261021` (worktree
`_worktrees/studio-hub-kh/fleet-render-finish-20261021`) over base `cd18d494` (0.6.194 closeout).
**Candidate only: unqualified and unreleased. Nothing is committed, built, installed or published,
and no render has executed.** No `native/*.swift` moves, so the native binary is unchanged; no build
was made. The coordinator's focused service/executor gate ran on this source (b4me9nf1k, exit 0,
60 run / 0 skip / 0 fail / 0 error) and the hub-regression wrapper was accepted (b6yl5ilp4;
underlying `.build/st02-hub-regression.log` line 32: 435 passed, 2 warnings, 35.88 s). Full gates,
independent review, packaging, a Gateway-to-Hub render, hardware qualification and receipt do not
exist.

- **The Hub can advertise and run the fleet's final-render worker.** A Mac with installed render authority publishes a `video.final_render` worker in its capabilities, and a controller republishes each joined leaf's row from one bounded authenticated read of the node's own capability route. The Gateway can therefore select a local Hub or a joined leaf by `physical_machine_id`. A worker with no authority is still shown, but with zero slots, `ready: false` and `drained: true` rather than invented capacity.
- **The render surface is closed and authenticated.** Requests are a bounded 1 MiB closed packet plus content-addressed assets uploaded in exact ranges (8 MiB chunks). Acceptance verifies every asset descriptor against the packet manifest; the dispatch key and exact body hash make a replay return the same job. Status, cancellation, output and acknowledgement are separate authenticated operations; output is served only by explicit byte range with an exact `If-Match` receipt ETag, and acknowledgement releases the asset references. Only the exact machine credential header is accepted; a caller-supplied namespace, cookie, proxy header or ordinary bearer token is refused.
- **Execution is the final pass, not model inference.** A claimed job runs the deterministic final-render compiler against a strict packet, then the two pinned universal ffmpeg and ffprobe payloads under the installed authority's resource bounds: process-tree RSS, cumulative wall time, minimum free memory and output size. The contract covers base video plus premixed audio, overlay layers, colour grade, vignette, film grain, aged-archive film, letterbox, presentation frame and logo; the candidate profile's envelope is 1920x1080 at 24 fps, up to 10,800 s, four assets and eight operations, with H.264/AAC MP4 output. It loads no model and makes no Gateway call.
- **One physical render at a time, through the existing scheduler.** Render work joins the broker's ranked FIFO rather than adding a second scheduler, and it obeys the existing pause, Lite Mode, maintenance/drain, quarantine, machine-enabled and exact-owner gates. Its durable compute lease is the same physical-machine authority the other lanes use, restored at startup. Queued and running render jobs appear in the Hub's activity/statistics snapshot.
- **A render survives interruption without guessing.** Jobs are recorded in the Hub ledger before launch; a claim is durable before the process starts; only one compute claim can hold a machine across scopes. Cancellation stops the exact process group and retains the compute lease until the stop is proved durable. After a restart, a launch or process whose outcome cannot be proved becomes `uncertain` and is never silently relaunched; a proved stop settles the row. The output receipt binds the accepted request, job, attempt, profile, tools, compiler and qualification digests.
- **What a Mac can encode is compiled, not guessed.** A qualified worker publishes a closed `encode` document derived from the same pinned compiler the executor runs: the contract's uncapped container/codec/effort/quality grid filtered by the exact tier rule (tier label, exact encoder in argv, no dropped burned-in features). Each live one-use qualification candidate carries its own document, because a qualification-only worker has no production authority for a worker-level answer. Admission compiles the real packet and refuses an encode this Mac cannot execute **before** any asset reference, permit consumption or ledger row; the executor applies the same rule again at launch. No tier-name parsing and no copied encoder table, so the advertisement cannot drift from the code that runs the job.
- **Retirement is move-only, and anything still needed is kept.** Aged, unreferenced uploads and their published bytes are renamed into a dated `_trash/retired/<UTC date>` folder (or the Hub's configured Trash Folder), never deleted. Assets that are referenced, being read, `recovery_required`, pending publication, or younger than their grace are retained untouched. Attempt folders retire only for a durably failed/cancelled job after the grace, and an `output_ready` attempt only after its ACK; a live compute, reader, transfer or ACK lease retains it, and the row is re-read after the caller's snapshot so a late lease closes the race. If the Trash root is unavailable, retirement is retained rather than silently relocated, the root is created only inside an existing real directory, and the reason is announced once.
- **Qualification is still pending, with a closed one-use lifecycle.** A live `render-authority/qualify.json` intent can admit one qualification render; the permit is bound at acceptance to the exact request-body hash, owner and qualification digests, and it is spent only inside the accepted row. A measured receipt is written only by a completed qualification run, and production is granted only by an operator copying that receipt byte-for-byte to `enabled/<hardware_class>.json`. This candidate writes neither: there is no receipt, no enabled Mac and no hardware render. The re-frozen `PROFILE.json` / `TOOLS.json` / `COMPILER.json` are worktree bytes only; no installed Hub or Gateway carries them yet.
- **Authority is off by default.** Production needs an operator-placed `render-authority/enabled/<hardware_class>.json` matching this Mac's exact hardware class; without it only a live, one-use qualification intent can run a qualification render, and its candidates are published with `production_eligible: false`. The packaged candidate profile declares `production_eligible: false`, no hardware qualification record and no fidelity record. **This is not a qualification claim.** No render has run on hardware, no receipt has been produced and no Mac is enabled by this candidate.
- Also in this candidate, from prior renderer-owned integration rather than this metadata lane: `components/hub/backend/activity.py`, `auth.py`, `broker.py` and `packaging/stage_voice.py`; the `packaging/render-authority/` descriptors are included as prepared. Tests and fixtures are added under `tests/test_render_*.py`, `tests/test_final_render_compiler.py`, `tests/hub_regression/cases/test_render_encode.py`, the fleet/capability regressions (including the qualification-candidate `encode` case in `tests/hub_regression/cases/test_capability_supply.py`) and `tests/fixtures/final-render-compiler-v1.json`; this metadata lane ran none of them — the coordinator's focused and regression runs are named above.

## 0.6.194 — 2026-10-21

### A Mac that has joined can keep itself up to date

When a Mac joins a controller it takes the controller's credential, but joining and switching a Mac
into the "node" role were always two separate steps. The part that copies a controller's update
onto a joined Mac was checking the role rather than the credential, so a Mac that had joined could
be left unable to fetch the very update that would let it report in. It now checks the credential —
the same test the rest of the fleet logic uses — so a joined Mac updates like any other, and a
controller never mistakes itself for a node.

### A Mac that can reach you now shows as alive

Older builds already make one call to their controller every minute — a plain "am I still listed?"
check. The controller accepted it but wrote nothing down, so a Mac that could reach the controller
could still sit on the Fleet page as one that had **never once been heard from**. That call is now
recorded as a sign of life, and only that: it does not make the Mac reachable, does not carry a
version or resources, and does not let it be given work — those still need a real report-in or a
successful check. A Mac that reports in as well keeps being treated as reporting in, so nothing
about how work is routed changes.

### Technical notes

Integrated on `release/0.6.194` (worktree `_worktrees/studio-hub-kh/release-194`) over canonical
main `439584a` (0.6.193), from `fix/shops-checkin-20261021`. No `native/*.swift` source moves, so
the app binary is unchanged and 0.6.193's binary is reused. **Gates have not run; nothing is
committed, built, installed or published.** Runtime outcome for the three Shops Macs stays
unresolved until live evidence distinguishes reachable / check-in-only / offline / unknown.

- **A joined Mac can mirror its controller's release.** The join records the adopted credential and the controller address but not the role, so the Hub's default `role` stays `standalone`, while `release_sync.controller_target()` required `role == "agent"`. A joined Mac was therefore a member of the group and still never mirrored the controller's release, so it could not pull the build that would make it visible — this can strand a joined Mac before its first check-in. `controller_target()` now keys on `fleet_nodes.membership()` (adopted credential + validated address) and excludes a `controller`.
- **A node's authenticated status call is recorded as presence.** `GET /api/hub/fleet/nodes/{id}/status` was authenticated but recorded nothing, so a Mac that could reach its controller still read `never_connected` for ever. It now stamps `last_status_at` (and `last_seen_at`, forward-only) for a node's **own** credential against a live roster row, and nothing else — no dial that works, no version, no resources, no pull eligibility. It **never overwrites** a `check-in`/`probe` reason, so `dial_is_failing` and the pull transport behave exactly as in 0.6.193. A group or Hub credential stamps nothing.
- **`never_connected` means "never heard from", by either direction.** It now clears on a check-in, a successful probe, or a status ask (its first-minute case included).
- Deliberately not changed: the pull transport, `dial_is_failing`, the check-in route's behaviour, and the `node`/`fleet`/`hub` auth scopes.
- Tests (added, not yet run): `tests/test_release_sync_target.py`, `tests/test_fleet_status_presence.py` (a real `create_app` route proof of credential scope). Accuracy-only wording in `tests/test_fleet_pull.py`, `tests/test_fleet_pull_end_to_end.py`, `tests/native/api_main.swift`, `docs/fleet-pull-channel.md`.

## 0.6.193 — 2026-10-20

### A Mac your main Mac cannot reach still works, and still shows as online

Three of the shop Macs sit on a different Tailscale account from the controller. Sharing a Tailscale
machine only works one way: the shared Mac can call out, but the Mac holding it can never call back.
So every check the controller made on those three Macs failed, and because the only thing that
counted as "this Mac is alive" was a successful check, all three sat on the Fleet page as machines
that had **never once answered** — for 51 hours — while they were switched on and reporting in every
minute.

They are now heard the other way round. A Mac reports in on its own call home, and that call is
evidence it is alive: the Mac shows as **online**, its address is corrected if it moved, and the
Fleet page says how the controller knows it is there — it reached out to the Mac, or the Mac reached
in. A Mac that is simply switched off is unchanged and still warns exactly as before.

### Work reaches a Mac the controller cannot dial

Everything the controller used to do by reaching out — turning Lite Mode on or off, renaming a Mac,
changing when it starts, installing or testing a model, running a transcription — is handed to the
Mac the next time it reports in, which is at most a minute later. A setting is confirmed as soon as
the controller has it and says so plainly; work you are waiting for an answer to is waited for, the
same as if the controller had reached the Mac directly. A Mac that can still be reached the ordinary
way is never affected: the direct route is always tried first, and this path is only used when it
fails.

### Nothing breaks if only some Macs have the update

The two sides can be updated in either order. A Mac on this version talking to an older controller is
told the feature is not there and carries on exactly as it did before; an older Mac on this
controller is untouched.

### Technical notes

One tested feature branch was merged over verified main `bc84fe2` (0.6.192) in worktree
`_worktrees/studio-hub-kh/release-193`: `pull-fleet` (`efbf399`). `native/StudioAPI.swift` moves, so
the app binary is rebuilt. Design note: `docs/fleet-pull-channel.md`.

- **A Mac its controller cannot dial still works, because its own call home is now the channel.** Three Shops Macs reach their controller through Tailscale **node sharing**, and sharing works one way: the shared Mac can dial out and the sharer cannot dial back. `fleet_nodes.probe()` accepted **only** a dial as evidence of life and `note_seen` stamped `last_seen_at` only from one, so all three read `never_connected` for ever — measured **51 hours** — while they were switched on and calling home every sixty seconds. The roster address was written once at join and never again, so a Mac that moved could not correct it either.
- **`POST /api/hub/fleet/nodes/{id}/checkin`** (new) is the node-originated channel. It is accepted with the Fleet token **or** the node's own credential (a node credential may check in only for its own id, as `GET …/status` already did), stamps `last_seen_at` with the reason kept distinct (`last_seen_via` = `probe` | `check-in`), carries the Mac's **current** address, version, Lite Mode and its own answer to `GET /api/hub/resources?local_only=true` — the exact route the controller's probe dials, carried verbatim — and hands back the requests the controller could not dial. `never_connected` is cleared by either evidence, and a node is check-in alive while `last_checkin_at` is inside `CHECKIN_FRESH_S` (180 s = three missed ticks).
- **Every unit of work carries an idempotent command id and the node acks it**, so a command delivered twice runs once. `method` + `path` + `content_b64`/`content_type` is **exactly the HTTP request the controller would have dialled**, replayed on `127.0.0.1:<its own port>` with the node's **own** Hub token; the controller's Fleet token is stripped from the command and never reaches the node. A command is retired when the node acks it (`seen`/`results`) or when it expires (`expires_at`, 900 s), and an id the node has acked is never re-issued.
- **Dialling stays the first choice wherever it works.** `fleet_pull.PullTransport` is an `httpx.AsyncHTTPTransport` installed on the controller's shared outbound client (`monitor._client`), so every job dispatch, job batch, shared-voice fleet-sync, fleet-token rotation and model-memory call reaches the queue **without a single broker, Chat or Subtitle call site changing**: the transport dials first and, only on a transport error against a check-in-alive node, replays the same request through the queue. `probe` records the reason it last succeeded or failed as `last_seen_via`, so while that reads `check-in` the dial is known to be failing **now** and `dial_is_failing()` sends a relay or the transport straight to the queue — a dial already known to be failing is never given a job's whole timeout to fail in — and the moment a dial succeeds the reason goes back to `probe` and everything dials again.
- **Two answers for two kinds of request.** A settings change (Lite Mode, rename, startup, work policy) is answered **`202`** with `queued: true` and the command id: the Mac applies it on its next call home and nothing is waiting for a result, so a caller reading `ok: true` as "done" would be reading a promise as a fact. Work the caller expects an answer for (a test transcription, a picture analysis, a model install, a Trash listing) is waited for and answered with the Mac's own status and body.
- **A Mac that is up is no longer invisible to the scheduler.** `peers.one` takes the check-in snapshot directly when the dial is known to fail, so the peer poll sweep is not stalled — without it the Mac would read online on the roster and still never be given work.
- **`probe` reports `state: reachable` with `via: check-in`** on a Mac it cannot dial but whose call home is current, and the roster gains `last_checkin_at`, `checkin_address`, `checkin_version`, `checkin_workstation`, `checkin_startup`, `checkin_current_job`, `checkin_last_stop`, `checkin_resources` and `last_seen_via`.
- **Either side may ship first.** A controller older than this build answers **404** to the check-in, and a node that gets 404 does nothing at all and keeps calling `GET …/status`, so an old controller and a new node stay honest; an old node and a new controller are untouched, because a node the controller *can* dial never reaches the queue. The Gateway's flat machine row carries `node_via` (0.2.194) so a reader can tell "we can dial it" from "it is dialling in", and `_node_post` recognises `202 + queued` as the controller having taken the change rather than the Mac having applied it.
- Deliberately left dial-only, with the reason recorded in the design note: `peers.py`'s dead relay helpers (no caller and no matching route in this build), and `delivery.py`'s artifact receipt ack and `transcription_jobs.py`'s ack (they build their own `httpx.AsyncClient`, and are post-completion receipts on a job that has already run).
- Tests: `tests/test_fleet_pull.py` (25), `tests/test_fleet_pull_end_to_end.py` (5 — a real controller Hub against a black-hole address), the native api suite's own pull-channel block in `tests/native/api_main.swift`, and `tests/test_fleet_nodes.py` extended.

## 0.6.192 — 2026-10-20

### A scene pack that runs out of time now says so, once

When a scene pack used up its own time limit, the app treated it like a passing network hiccup: it
printed "retrying in 5s:" with nothing after the colon and started the whole wait again, several
times over. It now stops at that point and tells you why, using the same plain wording a single chat
job gets — how long the limit was and how it was worked out. Real network hiccups are still retried.
No message in the app can show a blank reason any more.

### A Mac that has gone offline no longer looks busy

If every worker on a Mac had gone away but a job was still assigned to it, the fleet view showed that
Mac as "working". It now shows **offline**, with the job marked as waiting, so you can see at a glance
that the job is waiting for the Mac to come back rather than being worked on.

### Technical notes

One tested fix branch was merged over verified main `fd27422` (0.6.191) in worktree
`_worktrees/studio-hub-kh/release-192`: `fix/pack-deadline-named` (`884553c`, on top of `5a105ab`). No
native source moves, so the app binary is unchanged.

- **A scene pack that runs out of its own time limit now stops, says so, and does not retry.** `_run_pack` had a terminal branch for a stalled worker but none for a read timeout, so the job's own deadline fell into the generic transient retry — it printed "retrying in 5s: " with an **empty reason** (httpx's read-timeout message is empty) and re-ran the whole deadline up to `MAX_TRIES` times. A read timeout once the pack's own computed deadline has elapsed is now terminal, under the code **`CHAT_DEADLINE_REACHED`**, with the plain arithmetic reason from `chat_deadline.deadline_reason` — the same wording the proxy lane already uses. A connection timeout and other transient errors still retry.
- **No retry or terminal line can print an empty reason any more.** When an exception carries no message, the line falls back to the exception's class name.
- **A Mac that is completely offline but still holds a job reads "offline", not "working".** `activity._state_at` checked for work before it checked whether every studio on the Mac was down, so a Mac whose workers were all gone but whose queue item was still open showed as working. The fleet's existing all-studios-down rule (no new threshold) now comes first; the held job stays named as `current_job` and the fleet row carries **`job_waiting: true`**.
- Tests: `tests/test_chat_job_deadline.py` pins the terminal deadline stop, the retry of a connection timeout, and the never-empty reason; `tests/test_fleet_health_idle.py` pins the offline-with-job-waiting row; `tests/test_hub_stall_stop.py` follows the new state order.

## 0.6.191 — 2026-10-20

### A job that stops making progress is stopped, and you are told why

If an Image or Voice job running on this Mac freezes — no progress at all, not just slow — the app
now stops it and says so plainly, for example "stopped: no image progress for 63 minutes". Until
now it was only quietly noticed: the part of the app that checks for this existed, but nothing ever
called it, so the job sat there until you cancelled it. The waiting is generous: **an hour for an
Image job and two hours for a Voice job**, because Voice only reports progress between long sections
of narration. A job that is still moving is never touched, and work running on another Mac is left
to that Mac.

A job is only marked stopped once the worker has actually taken the stop, so you will not see a job
reported as finished while it is still holding the Mac.

### Smaller fixes

The app's quit-and-relaunch helper now waits the full time it says it waits. Its old arithmetic
counted whole seconds from when the shell started, so a two-second wait could be as little as one
second — and it could ask a Hub that was still closing down to quit immediately. The tests that
guard this, and the flaky ones around them, no longer fail at random.

### Technical notes

One tested fix branch was merged over verified main `2868be4` (0.6.190) in worktree
`_worktrees/studio-hub-kh/release-191`: `fix/flaky-gate-tests` (`25c9299`), which also carries the
merged `fix/fault-injection` (`0a09916`). `native/StudioHub.swift` moves, so the app binary is
rebuilt.

- **A local Image or Voice job that stops making progress is now actually stopped.** `components/hub/backend/automatic_recovery.py` was adopted with the rest of the source Hub's backend, but its caller lived in `main.py`, which the adoption records as an excluded entry point — nothing in the packaged Hub ever called it, so a local job whose own progress froze was only *flagged* (`stall.py` deliberately never acts). `broker._stop_a_stalled_local_job` is that caller: it observes the local job's own counters on the poll the Hub already makes, tells the worker to stop when the policy says eligible, and ends the item with a plain reason ("stopped: no image progress for 63 minutes") under its own code, **`STALLED`**. A peer's job is left to the peer, whose own Hub runs the same check.
- **The windows are the module's measured budgets — 1 hour for Image, 2 hours for Voice — not Chat's five minutes.** Voice progress moves only at long-form *section* boundaries and one measured Breeze section took 1,747 s on an M1, so a five-minute window would stop healthy narration. A job that keeps moving, or has not yet reached its budget, is left alone.
- **Image progress was read from a field nobody writes.** The observer named `current_step` / `total_steps`, which no worker in this app has ever published — `components/image/app/backend/generation.py` writes **`denoise_step` / `denoise_steps`** — so the stage evidence was false for every Image job and the observer could never have fired. The stage fields now live in one table, named after the workers that publish them.
- **A stalled item is only ended once the worker took the stop.** The signal goes first; an unconfirmed one leaves the item running for the next poll. Before this, a signal that never landed would have reported a dead job while a live one still held the Mac, and the durable phase would then read as `cancel_requested`, which the policy treats as protected and never retries.
- **The activity timestamp is the item's own last progress** (the merged `fix/fault-injection`). `components/hub/backend/activity.py` follows the item rather than the queue around it, so a stall is judged on the job's own silence.
- **The flaky gate tests are fixed at the source.** The Voice child's teardown crash is isolated; the PID file is written aside and renamed, so a reader that waits for the path to exist can no longer find it empty; and the native quit wait counts **half-second ticks** instead of `SECONDS` arithmetic — `SECONDS` is whole seconds since the shell started, so `$((SECONDS + 2))` could be reached as little as 1.0 s later and the helper could SIGTERM a Hub that was still quitting normally, which was a real product bug as well as the source of the native test's timing flake. Environment leaks between test modules are fixed at the source rather than worked around.
- Tests: `tests/test_hub_stall_stop.py` (new) drives the stop through the real proxy and pins the reason, the durable record and the cases that must not stop; `tests/test_local_stall_stop.py` (new) pins the local caller; `components/voice/app/tests/test_native_executor.py` and `test_priority_mlx_models.py` carry the race and environment fixes.

## 0.6.190 — 2026-10-20

### Chat work gets the time it actually needs — and no more

Every chat job used to get the same time limit: thirty minutes, whatever it was. That was one
measurement applied to everything. A one-line question held the Mac for the full thirty minutes, and
the biggest job the app plans — a hundred-prompt scene pack — was cut off roughly half way through
because it genuinely needs about an hour.

The limit is now worked out from the job itself: how long that Mac takes to load the model, to read
the prompt, and to write the answer it is actually expecting — times one and a half, with a floor of
thirty minutes and a ceiling of four hours. The expected answer comes from what the caller asked for,
or from a pack's own number of scenes, or from the ninety-percent figure across the answers this
fleet has already produced — never from the maximum the model could emit, which would make every job
three hours long. Where a Mac has never run a chat job, the published figure for that Mac's chip is
used, and the job says which of the two it was.

### A chat job that goes quiet is stopped, and told you why

If a chat job writes nothing at all for five minutes it is stopped, with a reason that says so —
"stopped: no new text for 5 minutes" — which is a different outcome from running out of time. A
worker that reports nothing about its activity is left alone: not hearing from it is not the same as
knowing it is stuck.

### You can see the limit and where it came from

A chat job (and a pack) now carries the limit it was given and the working behind it, so the job
detail shows both the number and how it was reached, instead of an unexplained thirty minutes.

### Technical notes

One tested feature branch was merged over verified main `5aab40e` (0.6.189) in worktree
`_worktrees/studio-hub-kh/release-190`: `feat/dynamic-chat-deadline` (`3968341`). No native source
moves, so the app binary is unchanged.

- **A chat job's time limit is now sized to the job instead of being one flat number for everybody.** The lane's flat 1,800 s came from one real measurement — 30 scenes at ~403 tokens each on an M1 — and was then applied to every job. It cut the longest job the fleet plans, a 100-prompt scene pack (~4,072 s on an M1), roughly in half, while holding the same Mac for the same thirty minutes for a one-line question. `components/hub/backend/chat_deadline.py` now computes the limit from the job itself: model load, plus the prompt at this Mac's prefill rate, plus the expected answer at this Mac's **measured** decode rate — the worker's own stated `generation_tps`, which the Hub already polls every five seconds — times 1.5, clamped to **1,800 s floor and 14,400 s ceiling**. Where a Mac has never run a Chat job the stated per-chip figure is used, and the job's note says which of the two it was.
- **The expected answer is a real expectation, never the ceiling.** It is the caller's `expected_output_tokens` when sent, else a pack's own scene count, else the p90 of the fleet's own 1,122 measured answers. `max_tokens` is deliberately not used: it is a ceiling, and sizing from it would make every job three hours long.
- **A job that stops writing is stopped, and says so.** A chat job that produces no new token for **five minutes** is stopped with "stopped: no new text for 5 minutes", distinct from the deadline stop in both words and code. The signal already existed — the chat worker publishes `generation_tokens` on every token. Where a worker publishes no activity at all the stall never fires, because an unseeing Hub is not a quiet worker.
- **The pack lane's flat 600 s read timeout was the same defect one lane over** and is now the pack's own computed limit. `deadline_s` and `deadline_note` are recorded on the job row and the pack row, so the limit and the arithmetic behind it are readable where the job is — which is what the job detail shows.
- Tests: `tests/test_chat_job_deadline.py` (new) pins the sizing arithmetic, the floor and ceiling, the per-chip fallback and its note, the stall stop and the unseeing-worker case; `tests/test_chat_priority.py` and `tests/test_hub_job_deadline.py` follow the new limit.

## 0.6.189 — 2026-10-19

### A Mac is no longer asked for memory it can never have

The app decides whether a Mac has enough free memory to start a model, and asks it to wait if it
does not. It was also asking that of Macs whose whole installed memory could never clear the
number — a floor of more than 8 GB on an 8 GB Mac, say — so the job sat waiting behind a Mac that
was never going to take it. Those Macs are now skipped, and a Mac that is merely busy or short of
free memory right now still waits exactly as before.

### The memory a voice model asks for is the number its own row publishes

Two numbers were in play for the same model: the memory measured and published on the model's own
row, and a formula worked out from the model's size. The larger of the two won, so VoxCPM2 4-bit
asked for **3.55 GB** when its own row says **2.8 GB** — and the app would send a job to an 8 GB Mac
that the voice worker then refused, saying it needed about 3.5 GB free and had about 3.4. The row's
own number is now the one used. The formula survives only where nobody has measured the model yet.

### One number, one home

The same free-memory figure also had a second copy in a table inside the Hub, and that copy was the
one actually enforced — so editing the model's own row quietly did nothing. The table now holds only
the different fact it is qualified for (a model's *total* memory need), and the live-free figure
comes from the model's row alone.

### The Models pane tells you whose number is in force

Under "Memory to start a job" each model now shows **Free before start** and its **Source** — "model
default" when it is the model's own figure, or "your setting" when you have overridden it. While
your setting is in force there is a **Reset to default** button, which puts the model's own number
back. That override belongs to this Mac and is stored nowhere else, so this screen is the only place
it can be seen and the only way back.

### Technical notes

Two tested fix commits were merged over verified main `7c4b233` (0.6.188) in worktree
`_worktrees/studio-hub-kh/release-189`: `fix/hunt-fixed-numbers` (`2c7b3bb`, on top of `de4e736`).
`native/StudioAPI.swift` and `native/StudioViews.swift` move, so the app binary is rebuilt.

- **The Voice worker uses the floor the model's own row publishes.** The worker published its row's measured floor (`min_free_memory_gb`) *and* a formula (`size_gb + 1.25`) under `required_free_memory_gb`; `workload_policy.required_free_memory_gb` takes the strictest of the two, so the formula won. For VoxCPM2 4-bit (row 2.8 GB, from the 8 GB M1+M2 runs that started cleanly at 3.25 GB free) that was **3.55**, and the Hub admitted 8 GB Macs the worker then refused — live as *"needs about 3.5 GB free and has about 3.4 GB free"* on two 8 GB Macs, 126 attempts and 3,068 `INSUFFICIENT_MEMORY` hand-backs. `_required_free_memory_gb` now prefers the published floor; the estimate survives only for a row nobody has measured, as `AGENTS.md` already required ("a floor must never be a formula").
- **A Mac that can never meet a floor is skipped, not waited for.** `broker._memory_gate` answered `wait` for every unmet free floor, including one at or above the machine's whole RAM — which can never be met, since macOS and the workers are resident — so the item parked behind a Mac that would never take it. It now skips that Mac, judged against its memory **class** (`broker.memory_class_gb`, the shipped size: a Mac reporting 8.59 GB is an 8 GB Mac), and still waits when the Mac is merely short right now.
- **One home for a model's live-free floor.** `memory_admission._FLEET_DEFAULTS` carried live-free numbers as well as each model's own catalog row, and `describe` gave the table precedence, so an edit to the row was silently ignored — two copies of one measurement with only one of them enforced. The table now keeps only the **total**-memory measurements, which are a different fact; `describe` takes the live-free default from the row alone (`max(2.0 GB, the row's published floor)`) and reports `min_free_source` — `operator_override`, `model_default`, or `default` for a model that publishes none, because the generic 2.0 GB must not read as a number the model stated. `monitor._assemble_aggregate_catalog` puts the enforced floor and its owner on every aggregate row as `hub_free_memory_gb` / `hub_free_memory_source` (never `min_free_memory_gb`, which `workload_policy` reads back).
- **The Models pane says whose number is in force.** Under "Memory to start a job" the app now shows "Free before start: 2.8 GB" with "Source: model default" or "Source: your setting", and **Reset to default** while an override is in force (a `PUT /api/hub/memory/admission` with no `min_free_memory_gb`, which the route reads as "clear it"). The override is site-local and on no row, so the Models screen is the only place it can be seen and the only way back to the model's own figure.
- Tests: `components/voice/app/tests/test_mlx_memory_registry.py` gains the registry guard (every row that publishes a floor is the floor the worker enforces, cold and loaded); `test_generation_artifact_contract.py` asserts the published floor rather than the formula; `tests/hub_regression/cases/test_memory_admission.py` gains the never-reachable-floor case (an 8 GB Mac against Fish Audio S2 Pro 4-bit's real 8.4 GB floor), the class arithmetic, the row-edit case, the override-wins-and-reset-returns-to-the-row case and the aggregate row carrying the floor with its owner. All new cases proved red on the old code first.

## 0.6.188 — 2026-10-19

### Chat answers can be much longer

The chat lane has a last-resort output allowance for when neither you nor the model states a number
of its own. It was **16,384 tokens**; it is now **128,000**. A number the model states still wins,
and a number you ask for still wins over both — this only moves the figure the lane falls back to
when nobody names one.

It is a maximum, not a target: an answer still stops when it is finished, so nothing generates more
work than before. One thing to know: the lane's own time limit has **not** changed, so a reply that
would genuinely run to hundreds of thousands of tokens can still be cut by that limit before it
finishes.

### A model with a smaller window still answers you

If you send a chat message without asking for a particular reply length, and the model's own window
is smaller than the lane's default, the app used to refuse the request — the default looked like a
number you had chosen, and it did not fit. It now gives you the longest reply that model can hold
instead. If you *do* ask for a specific length that does not fit, that is still refused, with the
prompt size, your number and the model's window spelled out.

### A stuck chat job now tells you why

A chat job whose requested reply length is larger than any Studio offering that model can produce
used to sit in the queue saying "Waiting for a free online Chat Studio with this model cached" —
which was never going to happen. It now names the largest ceiling actually on offer and says the
request itself is the reason it is not moving.

### Captions are no longer cut short, so region detection keeps all regions

Before outlining the regions you ask for, the app asks the model for a detailed caption and then
looks for each region's words in it. That one call was asking for **256 tokens** — a quarter of what
the model itself states (1,024) — so on a busy image the caption stopped part-way through. A region
whose words would have appeared further down was then reported as not named at all, which looks like
a detection miss rather than a caption that ran out of room.

The caption now runs with the model's own stated budget, the same as every other call in that worker.
Region detection sees the whole caption again.

### Technical notes

Two tested fix commits were merged over verified main `f616698` (0.6.187) in worktree
`_worktrees/studio-hub-kh/release-188`: `fix/output-limit-audit` (`ac15d69`, on top of `7eea4ed`).
No native source moves, so the app binary is unchanged.

- **The chat lane's last-resort output allowance is 128,000 tokens, not 16,384** (the owner's 2026-10-19 decision: *"4096 is too small. all models write more than that. the minimum is at least 128K tokens."*). `FALLBACK_MAX_OUTPUT_TOKENS` in `components/chat/app/backend/llm_engine.py` is `128_000`. It stays the last resort only: a figure the model itself states always wins, and a figure the caller names wins over both. It is a maximum, not a target — an answer still stops at EOS, so raising it removes a cap rather than adding work.
- **The 1,800 s chat deadline is deliberately unchanged, and that gap is stated in the code.** The two numbers were tuned together on measured throughput (16,384 tokens in 1,800 s is 76 / 65 / 51 % on M1 / M2 / M4). At those rates 1,800 s buys about 21,500 / 25,200 / 32,100 tokens, so a caller that genuinely generates 128,000 tokens is cut by the **deadline**, not by the ceiling, roughly 2.0–3.0 hours before it could finish. Raising the deadline is a different decision with a different cost — a lane is held for hours and a broken job is noticed in hours instead of 30 minutes — and it is **not** taken here (`standalone.MODALITY_DEADLINE_FLOOR_S["chat"]` stays `1800.0`, owner decision pending). The comment records that whoever raises that floor next must re-derive it from this arithmetic in the same build.
- **A request that names no output allowance is shrunk to fit a smaller model's context instead of being refused.** The route resolves an omitted `max_tokens` to the lane's own published default, so by the time the engine sees a number it cannot tell "the caller asked for nothing" from "the caller asked for exactly our default"; that figure is therefore treated as the lane's own. When it does not fit, the lane now returns the room the context actually leaves (the longest answer the model can hold) rather than refusing — otherwise a model with a smaller window than the fallback, `gemma4-26b-a4b-qat-4bit` at 32,768, would refuse every caller that named nothing. A figure the **request** named itself is never shrunk: it is refused exactly as before, with the measured prompt count, the allowance and the model's own stated window.
- **A chat batch that cannot dispatch now says why.** `chat_jobs._eligible_studios` also returns the largest output ceiling any ready Studio serving that model publishes, and a batch whose request is above every one of them gets a queue note naming that ceiling and saying it is not waiting for a free Mac — instead of the old "Waiting for a free online Chat Studio with this model cached", which promised something that could never happen while the batch sat queued for ever. A Studio that is offline contributes nothing, so the sentence names what is on offer now.
- **The vision worker's detailed caption runs with the model's stated budget.** `<DETAILED_CAPTION>` was pinned to `max_new_tokens=256` while every sibling call in the same function — `<OD>`, `<CAPTION_TO_PHRASE_GROUNDING>`, `<REFERRING_EXPRESSION_SEGMENTATION>` — uses `limit`, the manifest's own `defaults.max_new_tokens`. Florence-2 base and large both state **1,024**, so this worker asked for a quarter of what the model states. The caption is not a display string: the `segments` task matches region words against it (`REGION_WORDS`), so a caption cut short silently drops regions — `segments[region]` becomes `None`, which reads as "the caption does not name this region" rather than "the caption was truncated". The call now uses `limit`, the same budget as the calls beside it.
- Tests: `tests/test_chat_dispatch_output_ceiling.py` (new) pins the ceiling note and the eligibility scan; `tests/test_chat_worker.py` gains the fallback, the shrink-to-fit and the caller-named refusal cases; `tests/test_vision_worker.py` gains `test_every_task_runs_with_the_models_own_stated_budget`, written as a mechanism rather than a value — it asserts every task ran with the manifest's stated figure, so a task that later grows a number of its own is caught rather than inferred.

## 0.6.187 — 2026-10-19

### Your Mac's Trash can now empty itself — and you can turn it on from here

Everything the app retires ends up in your Mac's Trash, so a Mac whose Finder never empties that
Trash slowly fills its disk until somebody does it by hand. macOS already has the setting for this
(Finder → Settings → Advanced → "Remove items from the Trash after 30 days"), but there was no way
to tell which Macs had it on.

The app now reads it and shows it, on the storage screen and in the fleet's node list: on, off, or
unknown when the setting could not be read at all. A Mac that has never touched the setting reads as
**off**, which is what macOS itself does.

A button turns it **on** — for this Mac, or for a node from the Gateway. It only ever turns it on;
there is no button anywhere that turns it off. After the write the app reads the setting back, so if
it did not stick you are told it did not apply rather than being shown a success. Finder is not
restarted, and nothing needs it to be.

### Output is kept 7 days, and the Trash Folder has a size limit too

Generated output now waits **7 days** (was 3) before it moves to the Hub's Trash Folder, and the 20 GB
of live output is unchanged — whichever comes first. The Trash Folder itself also gets a limit now:
**20 GB**, adjustable, and anything older than 7 days or over that limit is moved on to your Mac's
Trash, oldest first. The same 7-day rule now covers transcriptions, finished download folders and
subtitle inputs that outlived their job record.

Nothing is permanently deleted at either stage, and models are never expired. A Mac that never
changed these numbers picks up the new defaults by itself; a number you chose stays exactly as you
set it.

### The Models page has tabs, and the details pane is yours to size

The Models page is now split by what a model does — All, Image, Audio, Subtitle, Music, Chat — with
the count each tab holds, instead of one long scroll with a "Jump to" picker that only moved the page
up and down. A function your library has no model for simply gets no tab, and the empty "Video — 0
models" heading is gone with it.

The details pane can now be dragged **both** ways. It used to be capped so the divider would only
move in one direction; now the list has its own floor and no ceiling but the window, and the width
you choose is remembered the next time you open the app. With nothing selected there is no details
pane at all — the list takes the whole page instead of leaving an empty panel down one side.

### Technical notes

Owner rule (2026-10-19): generated output moves to the Hub Trash Folder after **7 days or 20 GB**, and
the Trash Folder hands itself to macOS Trash after **7 days or 20 GB**. Models are never expired.
Nothing in either stage is permanently deleted. Three tested feature branches were merged over
verified main `32c7abf` (0.6.186): `feat/retention-7d-20gb` (`55841d7`),
`feat/macos-trash-auto-empty` (`20dbf9a`) and `feat/models-page-tabs` (`0cf5c7a`). All three touched
`native/StudioViews.swift` and the changelog; the retention and Trash branches also both touched
`native/StudioAPI.swift` and `components/hub/output_storage.py`, and the merge keeps every side's
added lines.

- **Generated output is kept 7 days, not 3.** `DEFAULT_RETENTION_DAYS` is 7 (`components/hub/standalone_config.py`). The 20 GB live-output budget (`DEFAULT_MAX_BYTES`) is unchanged: whichever limit is reached first, `output_storage.enforce` moves the oldest eligible output to the Trash Folder, exactly as before.
- **The Trash Folder now has a size cap as well as an age.** `trash_handoff_gb` (device setting, default 20) is new. `output_storage.trash_handoff(days, max_bytes)` hands items to macOS Trash when they are older than `days` **or** when the unified Trash Folder — every root `_trash_bases()` names, including a managed output root's own `.studiohub-trash`, so it is counted honestly — is over `max_bytes`; over-cap items go **oldest first**, until it is back under. It runs on the existing cleanup timer through the existing `trash_to_macos` helper, in the same pass as the age rule. `over_cap` is measured from what actually left, so a refused move is reported as still over. The reusable Trash Folder stays and nothing is permanently deleted.
- **The other stores follow the same 7-day rule.** `components/hub/backend/transcription_jobs.py` and `components/hub/backend/job_storage.py` default to 7 days (`POLICY_VERSION` 4); model-download run folders already read the shared `retention_days`, so they follow it too; a subtitle input whose job record is gone retires after `manager.INPUT_RETENTION_DAYS` (7, was `job_history.RETENTION_DAYS` = 90). The rotated service log is already retired to the Trash Folder rather than deleted, and the live log is still truncated only under red. Models and model caches are never aged or size-expired.
- **Migration: a Mac that never changed the setting gets the new defaults.** `standalone_config._DEFAULTS_VERSION` is 3, and `_LEGACY_DEFAULTS` now names each version's own defaults, so a file stamped with an older version whose value still equals that version's default was never chosen and reads as the new one: an untouched `retention_days: 3` at `defaults_version` 2 becomes 7, and the pre-0.6.117 30 days / 50 GB / 1 GB still becomes the current defaults. A value the owner chose — any other number — is kept, and every save stamps the version so it is never migrated again. The same pattern is used for the transcription/job-storage policy version (3 days at version 3 → 7 at version 4).
- **The app shows both stages with days and GB.** The storage-limits editor and the storage screen read "Move to Trash after 7 days or 20 GB" and "Send Trash to macOS Trash after 7 days or 20 GB", with an editable GB field for the Trash cap (`NativeStorageLimits.trashHandoffGb`). Every user-visible copy that said 3 days is corrected, including `PRODUCT.md`, `README.md` and `AGENTS.md`.
- Tests: `tests/test_trash_controls.py` gains the size-cap cases (oldest-first until under the cap, every root counted while only the Hub's own items move, a refused move leaves the item and still reports `over_cap`, neither rule eligible moves nothing), the 7-day/20 GB defaults, and the versioned migration cases; `tests/test_output_policy_defaults.py` gains the version-3 → 4 migration cases; `tests/test_output_trash.py`, `tests/test_hub_standalone.py` and `tests/test_storage_budget_loop.py` expectations are updated.
- **macOS's own "Remove items from the Trash after 30 days" is now visible and can be turned on from the app and from the Gateway.** Everything the Hub retires ends in macOS Trash, so a Mac whose Finder does not empty that Trash fills its disk until someone does it by hand — and across ~53 Macs nobody could tell which ones were set. The Hub reads the preference (`com.apple.finder FXRemoveOldTrashItems`) as the logged-in user, publishes it as `macos_trash_auto_empty` in the `startup` object every Mac already reports (`GET /api/hub/startup`, `/api/hub/resources`, fleet node rows; `trash_overview()` carries it too), and a node that goes quiet keeps its last word. A key that was never written is **off** — macOS's own default, a real answer — while an unreadable preference is `unknown`, never drawn as off. `POST /api/hub/macos-trash-auto-empty/enable` (and, for a node, `POST /api/hub/fleet/nodes/{node_id}/macos-trash-auto-empty/enable`) turns it **on**, never off: there is no route that disables it. The write goes through `defaults write … -bool true` and the answer is the value **read back**, so a write that did not stick is a refusal (`NOT_APPLIED`), not a success. The Hub writes through cfprefsd — the same path Finder's own Settings pane uses — and **does not restart Finder**; nothing about this needs one. A node older than this release answers the fleet route `HUB_TOO_OLD`, naming `0.6.187` as the version it needs (`standalone.MACOS_TRASH_HUB`).
- Tests: `tests/test_macos_trash_auto_empty.py` (the read, absent-is-off, unknown-on-a-failed-read, the one-way write with the read-back check, the route's fleet allowlist, and the `startup` publication) with `defaults` faked throughout — no test touches this Mac's real Finder preference.
- The Models page is tabs by function, not one long stack of sections. The owner: *"it has no tab or proper spacing. it's squished and also ugly."* A segmented control — the same control the Jobs page's history uses — now reads `All 11 · Image 2 · Audio 3 · Subtitle 2 · Music 2 · Chat 2`, with the count each tab would show under the current search and "Installed only". It replaces the "Jump to" picker, which only scrolled and hid nothing.
- A function the library holds no model for gets no tab. The empty "Video — 0 models" heading the owner pointed at is the same row of nothing as a tab that only says zero, so neither is drawn. The one exception is the Studio the operator arrived from: the generator pages' "Manage … Models" rows still land on their own Studio's tab, and a tab that vanished under the click that chose it would read as a broken button.
- Rows are one shape: the icon, the name, one second line, and the status badges given their own room at the trailing edge instead of being pressed against it. Inside a single function's tab the second line no longer repeats the tab's own name — it carries the size the catalogue states (`1.70 GB`), which is the same figure the details pane labels "Estimated size".
- The details pane is a two-way resizable split. The owner: *"it should work both ways because more often than not, I want to see details more than the model name."* It was an `HSplitView` capped at 380 pt with the list floored at 380 pt, so the divider would only move one way. The list now has a floor of its own (360 pt — a model name and its badge) and no ceiling but the window's, the divider is dragged either way with the resize cursor, and the chosen width is remembered across launches (`studioHub.modelsListWidth`).
- Nothing selected, nothing to inspect: the details pane is not drawn at all and the list takes the whole width, instead of an empty "Select a model…" panel across a third of the page. Selecting a model brings the pane back at the remembered width.
- A long published bound wraps instead of being cut off with an ellipsis — "up to 40000 characters · required" was rendering as "up to 40000 characters · r…" in a narrow details pane.
- Tests: `tests/test_native_model_defaults.py` pins the tab bar, the tab set read from the library, the filtering, the two-way split and the remembered width; `tests/test_audio_layout.py` follows the split's new name. `tests/native/view_checks.swift` draws the real Models page offscreen and checks its segmented control, and can write it to a PNG with `STUDIOHUB_VIEW_CHECKS_MODELS_PNG`. `native/render-page.sh models` with `STUDIOHUB_RENDER_FIXTURES=1` shows the page with a library in it.

## 0.6.186 — 2026-10-19

### The Trash Folder is now yours to see and control

Every Mac keeps a Trash Folder where things it retires go — cleanup, model swaps, old releases. The
storage screen now shows it: where it is, how many items are in it and how much space they take, with
a button to open it in Finder and a button to move its contents to your Mac's Trash. The move tells
you how many bytes it is about to move before you confirm, and if anything cannot be moved it stays
where it is.

The Trash Folder no longer grows forever on its own: folders older than the handoff age (7 days by
default, adjustable in the app) are moved to your Mac's Trash on the cleanup timer.

Nothing the app retires is permanently deleted any more — the rotated service log, finished model
downloads, expired subtitle inputs, removed shared voices, cleared transcriptions and failed policy
writes all go to the Trash Folder first. A Trash Folder an earlier build left somewhere else is
adopted at start-up, so nothing is stranded.

### A node can show the work it did for its controller

If this Mac runs as a Node, its own Jobs page now lists the Image, Music, Voice and Video work its
controller sent it. Before, those finished files were on this Mac but its history was empty, because
the job record lives on the controller. The row appears when the job actually finishes.

### The Jobs page can split results by Mac

On a controller, a new "Made on" control filters the Jobs page to All, This Mac, or Nodes, so you can
see what each Mac in the group produced.

### "Show in Finder" always works

The model library's "Show in Finder" buttons now create the folder if it is not there yet and then
open it, so you can copy models into a brand-new Mac by hand. A folder on a drive that is not
connected still tells you why it cannot be opened instead of the button looking dead.

### A failed model-list read no longer blanks the page

If the app cannot read the Hub's model list for a moment, it keeps the last good list instead of
marking every model as unsupported and greying out its Download button. When there has never been a
successful read, the page says it could not read the list and is retrying, rather than blaming the
model.

### Keep this Mac ready for one kind of work

Settings now offer "Keep this Mac ready for" a function of your choice, with a hold of 1 to 3600
seconds after its last job of that function finishes. It is **off by default** and set per Mac. A
running job of any kind always finishes first, a new job of the chosen function starts immediately,
and episode renders are never held or delayed. This schedules work — it does not promise a model stays
in memory, and memory pressure still releases as it always did.

### The App Update Key screen says what it stored, and why an update was refused

If the key you paste is actually the key's **fingerprint** — which is the same 64 characters long, so
nothing could tell them apart — the app used to accept it and then quietly refuse every update. It is
now refused outright, and the message says to paste the Public key from Gateway → Settings → App
Update Key instead of the fingerprint printed beside it. The stored key is shown by its own first
characters, not by its fingerprint, so you can see at a glance which one is installed.

A Mac that is being refused an update now says so on that same screen ("Updates refused: …") with the
reason, instead of looking merely out of date.

### An 8 GB Mac no longer offers Chat it cannot run

The Chat models state the memory they need (16 GB for the shipped one), and an 8 GB Mac was still
advertising Chat as runnable, so the Gateway kept offering that model for install on Macs that
cannot run it. An 8 GB Mac now reports Chat as not runnable, with the same sentence it already
showed as a warning: "This Mac has 8 GB of unified memory… Run it on a 16 GB or larger Mac." Nothing
is downloaded or attempted there.

### Technical notes

- The Trash Folder is now visible and controllable from the app. The storage screen shows its path, how many items are in it and how many bytes they hold, with an "Open Trash Folder" button and a "Move Contents to macOS Trash" confirmation that names the bytes it will move. `GET /api/hub/trash` and `POST /api/hub/trash/move-to-macos-trash` are the Hub's own local routes (authenticated, no delete route); the listing covers every root, including a managed root's own `.studiohub-trash`. `components/hub/output_storage.py` gains `trash_overview()`, `trash_handoff(days)` and `adopt_legacy()`.
- The Trash Folder hands itself to macOS Trash on a timer instead of growing without bound: `trash_handoff_days` (device setting, default 7, adjustable from the app) retires dated Trash folders older than that to macOS Trash on the existing cleanup timer, through the one same-volume `trash_to_macos` path. The stale "empties itself automatically" copy now describes that timed handoff.
- A Trash Folder an earlier build left outside the app's own data folder is adopted at start-up: the adoption is idempotent, same-volume only, collision-safe and records its provenance in `adopted-legacy.json`. A move that fails leaves the items where they are.
- Everything that used to delete now retires instead: the rotated service log (the live log is still truncated only under red), model-download run folders past retention, subtitle input folders whose job record has expired, shared-voice removals, transcription clears (batch, upload and uncertain attempt) and a failed policy write all move into the Trash Folder, and a failed move leaves the item in place.
- Tests: `tests/test_trash_controls.py` (19 cases) plus updated governor, settings and shared-voice expectations, and a native boundary check that a rotated log is retired rather than deleted.
- A Mac running as a Node now lists the work it ran for its controller in its own Jobs page. A controller's Image, Music, Voice and Video work reaches the node through the fleet proxy, and its batch lives on the controller, so the node's history had nothing to show even though it held the files. The node now files the worker's terminal `done` report (its model, prompt, output path and receipt) as a finished local row when the controller polls it; nothing is filed before terminal, so the broker can never recover and re-run it. `tests/test_studio_proxy.py::test_a_controller_job_that_finishes_is_listed_in_this_nodes_history` proves a running job is not listed and a finished one is, with its artifact path.
- On a controller, the Jobs page splits results by the Mac that made them: a "Made on" segmented control offers All / This Mac / Nodes. It reads the `machine` every job row already carries, so no new metadata was added, and it is offered only where a role of controller means more than one Mac's work can land.
- The model library's "Show in Finder" buttons are always usable. A folder that is not there yet is created with its parents and then revealed (the selected folder, or the resolver's default `~/StudioHubModels`), so models can be copied into a fresh Mac by hand instead of hunting for the folder. A folder on a disconnected drive still reports its own failure instead of the button looking dead. Which folder is resolved is unchanged.
- A failed `/api/hub/model-packages` read no longer blanks the app's package rows: the last good answer is kept, so a blip cannot make every model read as unsupported and disable its Download button until the next poll. When no answer has ever been read, the Models page says "Couldn't read the Hub's model list — retrying" instead of blaming the model's catalog entry, so the button is never silently dead.
- A Mac can be told to keep itself ready for one generation function of your choosing: "Keep this Mac ready for" a function and a hold of 1–3600 seconds after its last job of that function finishes (default 300). It is **off by default**, per Mac, and the node owns the clock — only the remaining seconds cross the wire, so a controller never counts down a peer's window itself. A running job of any kind always finishes first and is never preempted, a new job of the chosen function ends the hold immediately, and an episode render is never held or delayed. This is coordinated scheduling, not a warm-model promise: memory pressure always releases, `POST /api/memory/release` stays unconditional, a saved explicit `Immediate` mode stays authoritative, and a crash or restart still releases. A malformed stored row reads as off. The preference lives in `machine_flags.json` beside `priority_order`, is published on `/api/hub/work-policy` and `/api/hub/resources`, and an older Hub publishes neither field and reads as off everywhere.
- Pasting the App Update Key's **fingerprint** into the key field is now refused with what to paste instead. A fingerprint is a SHA-256, so it is 64 hex characters too and parsed as a perfectly good 32-byte Ed25519 public key: it was stored as the key, and then every release was refused silently — one Mac sat wedged on 0.6.181 for days, with nothing on screen saying why. `set_trusted_publisher` (and therefore `POST /api/hub/release/trust`) now compares the pasted value against every 64-hex value this Mac knows as a *fingerprint* — the trusted record's own and the held descriptor's `publisher_key_id` / `controller_key_id` — and answers 400 "this looks like the fingerprint, not the key: paste the Public key shown in Gateway Settings → App Update Key".
- `GET /api/hub/release` publishes `last_sync` (the last controller poll's `ok`, `error`, `result` and `at`), so a Mac whose updates are being refused names its own fault. The Connections screen shows the stored key's first 8 characters rather than only its fingerprint, the key field's help says to paste the Public key from Gateway → Settings → App Update Key (never the fingerprint printed beside it), and a refusal is shown as "Updates refused: …".
- An 8 GB Mac no longer advertises Chat as runnable. Both published chat models state their own tier (`qwen3-5-9b-4bit` `minimum_unified_memory_gb` 16, `gemma4-26b` 32) and the Chat worker's host eligibility treated being below it as a warning while still reporting the host eligible, so an 8 GB Mac published `memory_eligible: true` and the Gateway went on offering the 16 GB model for install there. The below-tier branch of `components/chat/app/backend/main.py:_host_eligibility` now reports the host **not eligible** with the warning as its reason: readiness and `available` are false, `memory_eligible` is false, and a chat request made straight to the worker is refused 409 with that same sentence ("This Mac has 8 GB … Run it on a 16 GB or larger Mac"). This supersedes the 2026-09-30 split where the Hub's scheduler was the only wall.
- The Hub's published aggregate enforces a model's RAM floor even when the worker states no memory total. The Chat worker publishes no `memory.total_gb`, and `memory_admission.publish_floor` can enforce nothing without one, so the row went out as runnable on a Mac the scheduler had already counted out. `_assemble_aggregate_catalog` now falls back to the machine's own profile (`hardware_profiles.machine_hardware_profile`) exactly as the per-model eligibility path already did, and still invents nothing when neither the worker nor the profile states a total.

## 0.6.185 — 2026-10-18

### The app and the installer now use the same model folder

On a new Mac, the SSD installer used to save the models in a different place from the one the app
looked in, so the folder had to be re-pointed by hand afterwards. The installer and the app now use
the same folder. When you copy models from the drive, they land in the folder the app is actually
using, and the app shows that same folder.

The model-location row has a new "Show in Finder" button, so you can open the current model folder
straight from the app instead of hunting for it.

If an earlier setup left a model library inside your Downloads folder, the installer now moves it out
to the app's folder for you — the same one-time move it always made — and the app then reports that
folder too. And a settings file the app would ignore is ignored by the copy step as well, so the two
never disagree about where the models are.

### Technical notes

- The SSD installer and the app now agree on where a Mac's models live. One resolver (`components/model_library.py`) answers a single folder for both the kit's copy step and the app: a folder the operator saved in the app, then a non-empty `<data>/models`, then a library recorded at the canonical folder, then one recorded at the earlier kit's folder, then the canonical default. The native setup card, the Models page location row and the Settings row read the same order, so the copy step fills the folder the app actually offers instead of a second one.
- A "Show in Finder" control on the model-location row reveals the folder the app is currently using, so the folder no longer has to be found by hand.
- A library the earlier kit left inside `~/Downloads/model-library` is moved out of that protected folder to the folder the app offers — the same single move the pre-resolver helper made — and the app then reports the same folder, so app and copy agree again.
- Reading the app's own settings file no longer crashes the copy step when the file is not valid text: it becomes the same plain "this file is unusable" problem every other unreadable-settings case produces, never a traceback. A settings file the app itself would refuse (a symlink, a wrong owner, or a group/world-readable file) is treated by the resolver as "no saved choice" too, mirroring the app's own reader guards, so the two never name different folders.
- Tests: `tests/test_model_library_root.py` covers resolver precedence and the fail-closed settings cases; `tests/test_copy_models_for_this_mac.py` adds functional helper-to-resolver tests (a saved choice fills the chosen folder, a missing chosen folder stops the copy, the Downloads library is moved out, and the old-app fallback still fills the canonical folder).

## 0.6.184 — 2026-10-18

### Chat no longer refuses when this Mac is busy

If Studio Hub was already working on something else, Chat used to answer at once with "local is reserved for other work on this Mac" — even though this Mac was set to prefer Chat. Now a Chat send with a model chosen waits its turn instead: when the current job finishes, the message goes through on its own.

A send waits at most 30 minutes for the Mac, plus half an hour for the answer itself. Nothing is sent twice: if the send is accepted, it is never repeated. If you start Chat without picking a model, nothing changes — the send behaves exactly as before.

### Technical notes

- The app's own Chat page waits out a busy Mac instead of refusing it. `sendChat()` now opts a named-model completion into the Hub's bounded Chat queue (`X-StudioHub-Queue-Wait-Seconds: 1800`, the 0.6.157 opt-in): it waits for the Mac's current job to finish and then runs, instead of answering at once with 409 `MACHINE_BUSY` "local is reserved for other work on this Mac". The immediate path never read `priority_order`, so chat-first alone never removed the refusal.
- One fresh 32-lowercase-hex wire id per send names the request everywhere the Hub looks: the `X-StudioHub-Request-ID` header, the body `job_id` (which the Hub rewrites to the request id before proxying) and the activity poll id. The transcript turn keeps its own dashed UUID — `chat_priority._identifier` requires exactly `[0-9a-f]{32}`, so a turn id can never be the queue key. A send with no model named keeps the immediate path byte-for-byte: no queue headers, the 30-second default, the turn id.
- The client timeout is the app's own bound: 1,800 s wait + 1,800 s generation + 10 s margin, outliving the Hub's whole server-side window (`deadline_floor_seconds("chat")` = 1,800 s on top of the wait) while staying far below the 3 hours the Hub allows a background caller. No automatic retry, no fallback of any kind: one POST per Send, never a resubmit after a possible acceptance. Hub-side queue, release and lease semantics are unchanged.
- Native fixture tests re-pointed from the old turn-UUID identity to the wire id the queue contract requires: the held completion's body id is proven 32-lowercase-hex, the request-id header equals it, the visible activity belongs to it, the queue wait is the app's own 1800 s bound, every activity poll is scoped to it, and an unnamed send proves the immediate path (turn UUID as `job_id`, both queue headers absent). A new local-route regression (`tests/test_chat_priority.py`) proves a busy chat-first Mac queues the app's own opted-in send instead of refusing it, then completes it with `X-StudioHub-Machine` and `job_id == request_id`.

## 0.6.183 — 2026-10-17

### Chat can use a model only your other Macs have

Until now, Chat on a site's controller only looked at that Mac. If a Chat model was installed on the site's other Macs but not on the controller itself, the request was refused even though the site had the model. The controller now relays that request to a Mac that has it, picks the best one that is free, and answers as usual. Nothing changes when the controller's own Mac has the model.

A Mac that cannot be checked at that moment is passed over in favour of another that can, and the request is only refused when no Mac can be verified — never sent to a Mac nobody checked.

### Technical notes

- A controller now relays a Chat completion for a model only its nodes carry: `studio_proxy` calls `_relay_chat_to_node` for a POST completion that does not use the bounded queue opt-in, reusing the pack dispatcher's own step (a node whose catalog has the exact model cached, memory readiness, ranked arbitration, the controller's lease on that Mac), and posts to the node's own Chat worker. It returns to the ordinary local path when this Mac's own Chat has the model, when only local studios exist, or when no catalog holds it.
- A relay candidate whose catalog read goes stale or fails in the eligibility window is skipped, not dispatched: the loop's `entry()` result is `None`-checked before `broker.prepare_machine_memory`, so an unprovable Mac is passed over for the next eligible holder and a bare 500 can no longer be produced before anything is sent. Only when every holder is unprovable does the existing named `MACHINE_BUSY` refusal stand. An assumed empty entry would have dropped the model's memory floor to 0 and dispatched to a Mac nobody checked, so the skip is deliberate.
- The relay's refusal is a location refusal the Gateway already defers or re-routes on; a completed relay names the Mac that answered in the Gateway's chat lane (`X-StudioHub-Machine`, needs Gateway 0.2.183).

## 0.6.182 — 2026-10-16

### Nothing is deleted without you

Studio Hub no longer permanently deletes anything on its own. Old outputs, app copies and other things it retires now go to its Trash Folder, so you can still get them back. The Trash Folder row has a new "Show Trash Folder in Finder" button, and when the disk gets full the message tells you to empty the Trash Folder to free space.

You can also see and empty each Mac's Trash Folder from the Gateway app (Fleet > Machines > … > Trash…). Permanent delete always asks you to confirm first.

### Technical notes

- Nothing the Hub retires is permanently deleted automatically any more: trash purges, acknowledged reclaims, admission clean-up, the disk governor's trash cap and age purge, old app copies, release artifacts, staging folders, update fallbacks, release manifests/blobs and expired staged voice copies all move into the Hub Trash Folder (same-volume rename) or, in the native app, macOS Trash. A failed move leaves the item where it is. Retirement leaf folders are created with plain `mkdir` and retried, so two retirements never share a folder.
- The Trash Folder row gets "Show Trash Folder in Finder"; the red, stop, over-cap and admission-refused messages name the folder and say to empty it.
- Fleet routes on the controller-to-node path: `GET /api/hub/fleet/nodes/{id}/trash` (items, physical bytes, kind, age, totals, disk free), `POST .../trash/move-to-macos-trash` and `POST .../trash/delete` (confirmed, re-measured; a changed count or size is 409 `TRASH_CHANGED` with nothing deleted). Only `<trash_root>/<date>/<name>` is touched; links are never followed. An older node answers `HUB_TOO_OLD` (needs 0.6.182).
- A permanent delete from the Trash Folder no longer stalls the Hub: under the lock it only lists, measures (cached physical bytes, same as the listing) and renames the items into a `.deleting-*` folder; the removal runs after the lock is released, through directory descriptors so a swapped folder is never followed. Leftovers are swept by the next delete. Moving to macOS Trash also runs outside the lock.

## 0.6.181 — 2026-10-16

### Rejected narration takes are kept for listening review

When a narration section still fails its quality check on the last try, Studio Hub now keeps that final take instead of discarding it. You can listen to it together with the reason it was rejected and the settings that produced it.

The section still counts as failed, and nothing is used automatically. Kept takes are never deleted on their own.

### Technical notes

- Keep a Voice section's final rejected take as an inactive, review-only candidate when it fails the quality gate on its last try, with its exact text, measured rejection reason, settings and quality evidence.
- Bind the candidate to the failed Hub item in the same transaction (waiting briefly while Voice finishes saving) and serve it only through authenticated, byte-verified reads. The failed job's outcome is unchanged and nothing deletes candidates automatically.

## 0.6.180 — 2026-10-15

### Read model catalogs that include list defaults

Fix one cause of the “unexpected response for /api/hub/catalog” error: a model's list default can now be read with the rest of the catalog. This restores catalog loading for the reproduced response that could leave local controls unavailable.

Your existing model choices and fleet pauses are preserved. List defaults that have no supported editor remain read-only.

### Technical notes

- Decode typed array values in the native model catalog, including the published Vision regions default. The reproduced list default previously rejected the complete catalog and could leave local controls unavailable.
- Preserve scalar types and object rejection. Array defaults remain display-only and are excluded from editable option controls and submitted model parameters.
- Add an actual-catalog decoder regression with a portable Vision fixture and retained 43-model structural response. The independent local-read/readiness resilience issue remains separate.

## 0.6.179 — 2026-10-15

### Fleet controls remain available during a catalog error

If the local model catalog cannot be read, the Connections page can still refresh the joined-node roster. The page keeps the catalog error visible, and it does not turn an unread roster into zero nodes.

The page enables Remove only for a current, freshly read roster. If the connection changes, the roster is stale, or local services stop while the confirmation is open, the removal is refused with a visible message and no request is sent. Existing authentication, pauses, model readiness and fresh-roster requirements remain in place.

This is a source-reviewed release candidate. It is awaiting the normal main-branch, DMG, installation and fleet rollout checks.

### Technical notes

- Keep native fleet controls reachable when the model catalog read fails: Refresh Nodes can still read the authenticated roster, while model-catalog errors remain visible and generation controls retain their existing requirements.
- Show an honest unknown roster until a fresh roster read succeeds; stale rows cannot be removed. A removal confirmation records its connection generation, refuses after services stop or the roster becomes stale, and sends no DELETE in that state.
- Preserve existing authentication, fresh-roster, pause, data, and model-catalog requirements. This feature candidate is source-reviewed and QA-verified, but is not yet on `main`, installed, or fleet-rendering shipped.

## 0.6.178 — 2026-10-14

### Restore login startup supervision when opening the app

Opening Studio Hub normally now refreshes its enabled login registration. This repairs the loaded registration failure found on four Macs while keeping the running app, services and your choices intact.

If macOS cannot confirm a login-startup change, the app reports that uncertainty and keeps the registration file. It prevents a later change from being reported as successful until the app is opened again. Normal Quit and your fleet pauses keep their existing behavior.

### Technical notes

- Refresh an installed login-agent registration on normal default-data startup only when this app actually owns the instance lock. Exact-label bootout then unchanged-plist bootstrap repairs loaded initialization failures without kickstart or changes to services, preferences or fleet pauses.
- Serialize refresh with Start at login changes. Failed unload preserves the plist and reports failure; timeout or signal termination marks the registration outcome uncertain and blocks later conflicting mutations in this app process. Only normal exits 0 or known-absent 3 authorize removal.
- Add keyless lifecycle, ordering, timeout-settlement and signal-reason regressions; preserve write-only enable, agent/custom-data guards and crash-only supervision.

## 0.6.177 — 2026-10-13

### Remote Macs can prepare memory for music

The controller can now ask a remote Mac's idle workers to release their models before a YuE2 song. An authorization mismatch previously rejected that request and could skip a Mac even when it had enough available memory. Active work remains protected, and your memory-mode choices stay unchanged.

### Technical notes

- Allow authenticated fleet controllers to release idle models through the exact known-worker proxy endpoint before YuE2 jobs. Remote nodes previously refused this handoff with403, causing `memory_skip` despite sufficient reported RAM.
- Keep memory presets, busy-worker protection, node credentials, settings/policy writes and bulk release boundaries unchanged. Retired video write routes remain denied. Add real proxy/authentication regressions with isolated fixture credentials.

## 0.6.176 — 2026-10-13

### Complete activity for picture analysis and embeddings

The fleet can now see when these workers are preparing, waiting or processing a request. Work remains visible until it actually finishes, including when a caller disconnects. This gives the Hub the missing information needed to check whether a Mac is idle.

### Technical notes

- Publish authenticated direct activity for Embeddings and Vision through the existing fleet contract. Track accepted preparation and queued calls before their first wait, with actual inference threads owning running and terminal state.
- Preserve pending work during client cancellation, retain every live request and only the latest terminal row, and publish scalar metadata without input or output content. The Hub continues to require every registered reporter before claiming complete activity.

## 0.6.175 — 2026-10-13

### Slow model copies can keep going

A model share now stays available while file data is being sent. A large file that takes more than 30 minutes no longer lets an actively used share expire before the next file starts. Unused shares still expire, and LAN-only copies retain their saved files when a transfer fails.

### Technical notes

- Renew a valid LAN model share after each successfully sent nonempty file-body chunk, so continuous slow delivery can keep the scoped share available for the next file. Idle, expired and unknown grants retain their existing expiry and refusal rules.
- Preserve whole/range/multipart/HEAD responses, failed-send cleanup, exact pinned file scope and the two-stream transfer cap. Add actual FileResponse regressions for long delivery and expiry boundaries.

## 0.6.174 — 2026-10-13

### Fleet model copies stay on the local network

Gateway can now request a LAN-only model copy. If that copy stalls, verified files stay saved while the fleet waits for a copy source. It can resume without each Mac starting a new internet download.

### Technical notes

- Add an explicit LAN-only policy to fleet model copies, carried through controller relays, durable downloads and the package CLI. Peer failure preserves verified/staged bytes and cannot silently fetch internet files.
- Refuse malformed copy requests, incompatible source revisions and unverified companions. Strict retries retain accepted run identity; manual download fallback remains available.

## 0.6.173 — 2026-10-13 · Qwen Image 2.1 Turbo MLX 4-bit on the fleet

Qwen Image 2.1 Turbo Low-RAM MLX 4-bit is now available for normal model installation and image generation on Macs with 16 GB or more memory. It draws in eight steps and keeps the existing low-memory loading and cleanup. Regular Qwen Image 2.1 Low-RAM 4-bit stays available.

Generation speed and image quality are being compared through normal fleet jobs. The 16 GB minimum and starting-memory allowance are adopted operating limits; no new Turbo memory measurement or speed result is claimed yet.

### Technical notes

The owner adopted the pinned Turbo 4-bit package for normal Hub distribution and generation on 16 GB and larger Macs. It retains the official eight-step schedule, guidance 1, roughly-one-megapixel sizes, staged low-RAM loading, tiled decode and release after every job. The 8.0 GB free-before-start floor is inherited provisionally from regular Qwen Image 2.1 Low-RAM 4-bit, not a measured Turbo result. Turbo timing and image-quality comparisons remain runtime evidence; no measured speedup, memory peak or 8 GB support is claimed. Other candidates retain their production gates.

## 0.6.172 — 2026-10-13 · Explain dispatch waits and prepare Turbo 4-bit safely

### Clearer reasons when a job waits

The Hub keeps a short record of why each Mac could not take a waiting job, so support can diagnose placement without guessing. Qwen image progress also stays steady instead of briefly moving backward.

Qwen Image 2.1 Turbo 4-bit is included for isolated evaluation. It stays unavailable for production until real-Mac quality, memory, speed and cancellation checks pass. It is not an 8 GB model.

### Technical notes

The broker retains bounded, fixed-code evidence from the last blocked dispatch pass and the eventual selected Mac. Evidence survives terminal persistence; routing, priority, memory admission and owner pauses keep their existing rules. Gateway 0.2.176 exposes this evidence through an authenticated admin read.

- Qwen progress stays monotonic while publishing denoise iteration counts. A private qualification cancellation passes only after the denoise callback observes Cancel, the job ends cancelled without an artifact, and the worker releases memory and remains responsive.
- The separately pinned Qwen Image 2.1 Turbo Low-RAM MLX 4-bit package uses its official eight-step Euler schedule, guidance 1 and roughly-one-megapixel presets. Its prompt materialization, zero cache, component eviction and tiled decode reuse the existing Qwen adapter.
- Turbo remains production-disabled and physically unqualified. No 8 GB support, measured peak, free-memory floor, per-chip timing or speedup is claimed. Physical 16 GB qualification comes first using the private guarded kit.

## 0.6.171 — 2026-10-12 · YuE2 carries on at "warning" memory pressure (owner: "let them continue")

### Songs on 16 GB M1 and M2 Macs keep going

A song no longer stops just because macOS says memory is getting tight ("warning"). It still stops if memory becomes critical, if less than 2 GB is free, or if the Mac starts swapping.

### Technical notes

- On the 16 GB M1/M2 Macs YuE2 alone pushes macOS to pressure "warning", and the port's guard (`lyra.measure.GPUExecution._check_sample`) stopped about half the 3.5 min songs mid-way even after the Mac was cleared (M1 1302 at 577 s, M2 1103 at 460 s). Owner decision 2026-10-12: carry on at warning; still stop at critical, under 2 GB free, or when the Mac starts swapping.
- `yue2_runner.tolerate_warning_pressure` wraps the port's check once per run: a "warning" sample is passed on as normal, so every other stop stays the port's own (critical pressure, < 2 GiB available, the process over its budget, > 64 MiB of new swap-outs or > 128 MiB swap growth). The pre-load gate now refuses only critical pressure. Checked against the installed port: level 2 passes, level 4 still stops.
- Test: `WarningPressureTests` (fails on the old guard).

## 0.6.170 — 2026-10-12 · A song's own deadline, a bridge that stops when told, and no YuE2 start on an uncleared Mac

### Long songs get the time they need

A song's time limit now follows its length: twice what the slowest Mac needs for a song that long. A 3½-minute song gets about 54 minutes instead of 15. When a song is stopped, the Mac now really stops it straight away.

### Technical notes

- The music lane's flat 900 s cut 3–4 min YuE2 songs on the fleet (7 of 18 at 15:44, "did not finish within 900s … the Mac did not confirm it stopped"). Each YuE2 job now states `deadline_s` = 2 × (7.1 s per song-second × requested length + 120 s), the owner's rule: the slowest measured Mac (M1 16 GB, 1,415.5 s for a 199.5 s song) for everyone, doubled. The Hub copies it onto the batch item; Gateway 0.2.175 polls to the same number.
- `yue2.generate` enforces it: a watcher thread checks every 2 s for a cancel or the deadline and terminates the bridge. Before, the timeout argument was ignored and a cancel was only seen when the bridge printed a line, which it does not do for minutes while generating.
- `broker.prepare_machine_memory`: a YuE2 job is not started on a Mac whose idle Studios could not all release their models (`skip` → another Mac). `yue2_runner` fails fast when memory pressure has not returned to normal after its 120 s wait, instead of starting a song the port's guard would stop half-way.
- Tests: `tests/test_yue2_clear_machine.py` (deadline, watcher on deadline and cancel, skip).

## 0.6.169 — 2026-10-12 · YuE2 gets the whole Mac: idle Studios release first, then it starts at normal pressure

### Songs on 16 GB Macs no longer stop half-way

Before a song starts, the other Studios on that Mac put away the models they are not using, and the song waits a moment for memory to settle. Work that is already running is never stopped; the song simply waits its turn.

### Technical notes

- Measured 2026-10-12 after 0.6.168: YuE2 loaded on 16 GB Macs (studio-mac-1103-m2-16, studio-mac-1201-m2-16) and then failed 150–180 s into the song with `System memory pressure is not normal (level=2)` — the port's own guard (`lyra/measure.py`: pressure must stay 1, ≥ 2 GiB available, no new swap). The 24 GB studio-mac-1300 sang (60 s song, 230 s). On This Mac, releasing idle Studios before each song gave zero failures.
- `broker.CLEAR_MACHINE_MODELS` (`vanch007/mlx-Yue2-3B`): `prepare_machine_memory` asks every idle Studio on the target Mac to release its models before a YuE2 job, not only when free memory is short. A busy Studio is never preempted; the job waits its turn (`wait`), as one generation per Mac already makes it.
- `yue2_runner.wait_for_normal_pressure`: before loading, the bridge waits up to 120 s for `kern.memorystatus_vm_pressure_level` to read normal (unknown never blocks). The port's guard is unchanged.
- Tests: `tests/test_yue2_clear_machine.py`.

## 0.6.168 — 2026-10-12 · YuE2 can sing on the fleet: its package carries the licence file the engine requires

### Songs with singing work on your Macs

YuE2, the model that sings, could not start on any Mac: its download was missing one small licence file that the model checks for. The file is now part of it. Install YuE2 again on the 16 GB Macs and only that small file is fetched; the 9.9 GB already there is reused.

### Technical notes

- Measured 2026-10-12 (Gateway probe `f97b7ba8`, three Macs: 1300, 1100, 1200): every fleet YuE2 run failed at load with `Converted directory has missing or unexpected files: missing=['LICENSE']`. The port (`lyra/conversion.py`, pinned 9253ed13) requires the publisher's `LICENSE` in the converted tree; the capture's publisher-furniture rule dropped it, so YuE2 had never sung on the fleet.
- `model-manifests/yue2-3b-mlx.json` gains `model/LICENSE` (20,309 bytes, sha256 `060985741d20…` — the value the port pins); the capture entry keeps it (`keep: ("LICENSE",)`). 10 files / 9,920,189,064 bytes.
- `model_packages._download_one` hard-links the files an install of the **same revision** already holds into the new staging folder before downloading; each is still hash-checked, so a manifest that gains a file fetches only that file and writes no second copy. The old folder is left as it is. Test: `test_a_manifest_that_gains_a_file_downloads_only_that_file`.
- Test `ConvertedPackageCoverageTests`: a converted package's manifest carries every file its `conversion.json` lists (with LICENSE's size and hash). `yue2-vae` needs no LICENSE: the port loads the decoder by `config.json` + `model.safetensors` identity only.
- After this release YuE2 reads as not installed on every Mac until it is installed again (Gateway → Install to fleet `yue2-3b-mlx`); that install fetches the 20 KB file.

## 0.6.167 — 2026-10-12 · Staged voice copies last 4 days, so Gateway's 3-day reuse holds

### A voice is sent to each Mac once every 3 days

A cloned voice's copy on a Mac now stays for up to 4 days, so the Gateway can reuse it for the full 3 days you asked for instead of sending it again after about a day and a half. Old copies are still cleaned up on their own.

### Technical notes

- Owner decision 2026-10-11: a staged voice clip is reused for 3 days instead of being re-sent to every Mac each time. Gateway 0.2.168 asks the Hub to keep it 84 h (72 h reuse + 12 h headroom), but the Hub capped every staged copy at 48 h, so Gateway stopped reusing it after about 36 h.
- `execution_assets.MAX_TTL_SECONDS` 48 h → 96 h. The default (24 h) and the cleanup are unchanged: `cleanup_expired` still deletes each copy when its own `expires_at` passes, from the storage sweep.
- Test: `test_hub_voice_references` stages with an 84 h TTL and checks the copy keeps at least that (it got 48 h before).

## 0.6.166 — 2026-10-11 · Vision Studio: depth maps, subjects and outlines for Story's effects

### Your Macs can now look at a picture

Studio Hub has a new Vision Studio. Given a scene picture it says how far away each part is (a depth map), where the main subject is, where the other interesting things are, and outlines the sky, water, clouds and flames. Story Studio will use this for the new 3D-photo moves, zoom to subject, the guided tour and sky-only clouds. It runs on your own Macs with free models (Depth Anything V2 Small, 99 MB, Apache licence; Florence-2 base, 469 MB, MIT licence). A larger Florence-2 can be switched in later if the outlines need to be sharper. It never waits for a narration or an image job to finish.

### Technical notes

- Owner decisions 2026-10-11 (effects plan E2–E4): the depth model and Florence-2 are approved, run in our own apps on the fleet and are reached through Gateway KH (0.2.167 `POST /v1/vision/analyze`). New worker `vision` (`components/vision`, `packaging/vision_worker.py`), engine `vision-mlx`, modality `vision`, operation `image.analysis`, registered where 0.6.165 registered Embeddings. It runs on the shared Voice runtime and adds no package; no PyTorch runs in the Hub.
- Models (three manifests, data only): **Depth Anything V2 Small** (`depth-anything/Depth-Anything-V2-Small-hf` @ `5426e4f0`, 99 MB, Apache-2.0; Base/Large are CC-BY-NC and are not shipped), **Florence-2 base ft** (`florence-community/Florence-2-base-ft` @ `0b03b6f1`, 469 MB, MIT, the default) and **Florence-2 large ft** (`florence-community/Florence-2-large-ft` @ `26b734a5`, 1.55 GB, MIT, not installed by default: install its package and name it, no release needed). Safetensors only; no pickle is downloaded.
- `depth.py`: DINOv2-S/14 + DPT in plain MLX, the publisher's preprocessing (keep aspect, short side 518, multiple of 14). Matches transformers' `DepthAnythingForDepthEstimation` within 1e-4 relative on 7 real scene stills, and on random weights offline (`tests/test_vision_worker.py`).
- `florence.py`: DaViT + projector + BART in plain MLX with Hugging Face's beam search (3 beams, no repeated 3-grams, forced BOS/EOS) and the publisher's `<loc_*>` parsing. float32 throughout (float16 drifted ~20% at the image encoder and changed some outlines). Token-identical with transformers' `Florence2ForConditionalGeneration` on 21 of 21 real prompts (base) and 8 of 9 (large; the ninth parses to the same labels and boxes); boxes within half a bin (0.0005), outline IoU 0.98–1.00.
- `POST /v1/vision/analyze` on the worker: `{tasks: [depth|subject|points|segments], image: {b64, mime} | {url}, model?: id | [ids], text?, regions?}` → depth as an 8-bit PNG (255 = nearest, long side ≤ 518) with its p95−p5 range; subject `{box [x,y,w,h], label, score, source: grounding|detect}`; up to 8 points; one 8-bit outline PNG per region with coverage and score, plus the picture's detailed caption. Florence-2's referring segmentation outlines something for any word, present or not ("sky" on an indoor still life covered 86%), so a region is outlined only when that caption (~0.2 s) names it (`REGION_WORDS`); otherwise it is `null`. Coordinates are 0..1. The picture is encoded once for every Florence task; requests run one at a time on one MLX thread. Memory policy is Chat's.
- Peer route `/studio/vision/v1/vision/analyze` and a controller relay to a chosen node, `POST /api/hub/fleet/nodes/{id}/vision/analyze` (the Gateway's "analyse on the Mac that made the picture" rule). Both are admitted like other work (paused Mac, Studio, Lite Mode, every named model installed) and **take no machine lease** (`_PEER_UNLEASED_PATHS`). `ponytail:` no queue; add Chat's bounded wait if analysis ever contends with production.

## 0.6.165 — 2026-10-11 · Embeddings Studio: local text embeddings for Story's meaning check

### A free, local meaning check for ideas

Studio Hub has a new Embeddings Studio. It turns short texts into numbers that say what they mean, so Story Studio can tell when two ideas are the same idea in different words. It runs on your own Macs with a small free model (BGE small en v1.5 MLX bf16, 67 MB, MIT licence). It needs no outside app and costs nothing. A thousand idea titles take about a quarter of a second, and it never waits for a narration or an image job to finish.

### Technical notes

- Owner decision 2026-10-11 (ideas plan Q1): embeddings run in our own apps, not Ollama. New worker `embeddings` (`components/embeddings`, `packaging/embeddings_worker.py`), engine `embed-mlx`, modality `embeddings`, registered in `vocabulary.WORKER_FOR_MODALITY`, `_ENGINE_ROUTE`, `model_packages.ENGINES`, `hub_worker.COMPONENT_WORKERS` and `stage_voice.COMPONENT_STAGERS`. It runs on the shared Voice runtime and adds no package.
- Model: **BGE small en v1.5 MLX bf16** (`mlx-community/bge-small-en-v1.5-bf16` @ `0e415031`, package `bge-small-en-v1-5-bf16`, 10 files / 67,702,966 bytes, MIT). A BERT encoder written in plain MLX (`encoder.py`); it loads the publisher's safetensors by name, keeps float32 in memory, and pools and normalises as the manifest's `defaults` say (`cls`, normalised, 384 dimensions, 512 tokens). It matches Hugging Face `BertModel` to 5e-7 on the real checkpoint and to 1e-5 on random weights (`tests/test_embeddings_worker.py`, offline).
- Why this model: nomic-embed-text-v1.5 has no MLX build, its own weights are 547 MB fp32 (over the approved ~270 MB) and it needs a custom architecture with remote code. BERT is the architecture the bundled MLX runs most simply, and BGE small v1.5 scores clearly above all-MiniLM-L6-v2 on MTEB.
- `POST /v1/embeddings` on the worker (OpenAI shape: `{model, input}` → `{data:[{embedding, index}], model, usage}`); up to 2,048 inputs, each under 512 tokens (a longer one is refused by index, never cut). `[M]` M2 Max 32 GB: load 0.07 s, 1,000 short texts 0.24–0.31 s, peak MLX 486 MB, max RSS 155 MB. Memory policy is Chat's (`components/embeddings/app/backend/memory_policy.py` re-exports it).
- Peer surface `/studio/embeddings/v1/embeddings` (fleet credential allowed). It is admitted like other work (paused Mac, paused Studio, Lite Mode, model readiness, update drain) but **takes no machine lease**, so it runs beside a narration or image job instead of waiting behind it (`standalone._PEER_UNLEASED_PATHS`). `ponytail:` no queue; add Chat's bounded wait if batches ever contend.
- Downloaded once into this Mac's library (owner-approved). Other Macs get it only when it is installed on them.

## 0.6.164 — 2026-10-09 · An empty or broken narration file is a failed job, not a finished one

### An empty narration file is retried, not delivered

If a Mac produces an empty or broken narration file, the job now tries again, on another Mac when one is free, instead of being marked finished. Every finished narration file now comes with its size and checksum.

### Technical notes

- On 10-09 at 08:04 a narration job on studio-mac-1504 (Hub batch `ebcfd92c1a`) produced an empty (0-byte) m4a. The Hub noticed the file could not be checked, but still marked the job finished, following the rule "never retry a completed generation", and handed out the empty file with no size or checksum.
- Now an empty file, or one no player could open, is a failed try and not a finished job. The job then retries and moves to another Mac like any other failure. The error code says which: `EMPTY_OUTPUT` for an empty file, `INVALID_OUTPUT` for one that cannot be opened. That covers an m4a with no `moov` box or cut off partway, a WAV with no audio or cut off, and bytes that are not the type the Mac said it sent (for example an HTML error page).
- A file that plays but whose extra check could not finish still counts as finished, exactly as before. Examples are a size or checksum that differs from what the Mac reported, or a Mac that did not report the duration. It is still marked unchecked and not billed.
- Every finished narration file now comes with its size and SHA-256, including the unchecked ones. The Hub uses the bytes it downloaded. When the download could not run, it uses the size and checksum the Mac reported. Before, only the unchecked files were missing these, so the Hub served them with no `Content-Length` and no `X-Content-SHA256`.
- The m4a encoder now checks the file it writes: it must start with `ftyp`, contain `moov`, and have no box cut off. Before, it only checked the first box, and the length check is skipped when `ffprobe` cannot run. A broken encode now delivers the WAV instead, as an encode error already did.
- When this happens, the Hub log says which job and which Mac it was, and how many bytes the Mac reported.

## 0.6.163 — 2026-10-09 · Qwen thumbnails go to the fastest free Mac

### Qwen thumbnails go to the fastest Mac

A Qwen Image job now goes to the Mac that will finish it soonest, not the smallest one. A slower Mac only takes it when waiting for a faster Mac would take longer. A Mac where Image is switched off, or placed below another function, keeps that setting.

### Technical notes

- Owner decision: "Prefer fast Macs for Qwen." Inside a site, a Qwen Image job (any model whose id contains `qwen-image`) now goes to the Mac expected to finish it soonest. It used to go to the smallest Mac, which on sites 1100–1400 often meant an M1 at about 949 s while the M4 would take about 605 s.
- How long a Mac takes is its own median for that model from this Hub's finished jobs. Without that, it is the median for Macs of the same chip and memory. Without that, it is the 10-08 measurement for its chip: M2 Max 228 s, M4 605 s, M2 812 s, M1 949 s, and 949 s for an unknown chip. These are the same rule and numbers Gateway 0.2.156 uses to choose the site.
- A slower free Mac takes a Qwen job only if it would finish before a busy faster Mac would, after that Mac's current job and the Qwen jobs queued ahead of this one. Example: when This Mac is busy and nothing else is queued, an idle M1 leaves the job for it (342 s against 949 s). With three Qwen jobs queued ahead, the M1 takes it.
- While a job waits like this, the job says "Waiting for a faster Mac". The waiting Mac stays free for its other functions.
- Owner settings still win. A Mac with Image switched off gets no Image work, and a Mac that puts another function above Image is never waited for. Other models are placed exactly as before.

## 0.6.162 — 2026-10-09 · Three fixes from the overnight review

### Fewer narration jobs fail for no good reason

- A cloned-voice job sent to a busy Mac now waits for a free Mac instead of failing.
- The check that narration says its text no longer rejects a correct reading because a year or a joined word is written differently, such as "1880s" for "eighteen-eighties" or "wash houses" for "washhouses".
- A job that is waiting on a Mac without enough free memory moves on after 1 minute, instead of 4, when another Mac can run it.

### Technical notes

- A cloned-voice job no longer fails when the Mac it was sent to is busy with other work. That Mac answers "reserved for other work" before it even looks at the voice reference. The job now stays queued, skips that Mac for a few seconds, and doesn't use up one of its tries. This is how the rest of a job already handled a busy Mac. Overnight this caused 276 failed tries on 252 jobs, mostly at sites 1600 and 1500.
- The Breeze check that the audio says its text no longer fails a correct reading over how a word is written. "Eighteen-eighties" now matches Whisper's "1880s", "the eighties" matches "80s", and "nineteen fifty-two" matches "1952". "Washhouses" matches "wash houses", and the other way round. Joined and split words are only compared after the normal word match, and a wrong sentence still fails. The EP0028 sentence that failed 12 times on 3 Macs now passes.
- When a Mac doesn't have enough free memory to start a job and another Mac can run the same model, the job is handed back after 1 minute instead of 4. When this Mac is the only one that can run it, the job still waits 4 minutes.

## 0.6.161 — 2026-10-08 · Qwen Image 2.1 uses 20 steps by default

### Qwen images use 20 steps

Qwen Image 2.1 now uses 20 steps by default, up from 16, for both the regular and the Low-RAM version. In a thumbnail test, 16 steps was acceptable and 24 looked best, so 20 is the middle ground. Each image takes about a quarter longer. If you saved your own default steps for this model on a Mac, that Mac keeps your setting.

### Technical notes

- Qwen Image 2.1 now uses 20 steps by default (owner's choice after a thumbnail test; 16 was acceptable, 24 looked best). This covers both packages, the regular one and Low-RAM. The allowed range is still 1–50.
- A Mac where someone saved its own default steps for this model keeps that number. A request that names its own steps also keeps them.
- Each image job's time limit is still based on the steps the job actually runs, so a 20-step job gets a longer limit than a 16-step one.
- The two Qwen manifests were regenerated with the capture tool. Only their `defaults` changed. The files and install identity are the same, so installed copies don't need to be verified again.
- The kit readme no longer lists the retired SSD test runners (`RUN-16GB-FISH`, `RUN-8GB-TTS`).

## 0.6.160 — 2026-10-08 · Every model download shows on the Jobs page, in its own tab, with Cancel

### Model downloads in their own tab

Every model download on this Mac now shows up in the app, including ones started from the Gateway, with its progress and a Cancel button. They have their own Downloads tab on the Jobs page, so you no longer scroll to the bottom to find them. Cancelling keeps what was already downloaded, so a later install continues from there.

### Technical notes

- The app now lists every model download this Mac's Hub is running, not only the ones started from the app. A download started from the Gateway's fleet install (for example Qwen Image 2.1 Low-RAM) used to read "No model downloads" while it ran; it now shows its progress and a Cancel button on the Jobs page, the Models page and the Overview.
- Model downloads have their own tab on the Jobs page, after Chat, so they no longer sit at the bottom of All. The tab shows how many are running, for example "Downloads (1)".
- Cancel stops the download only. The part already downloaded stays in the library's staging folder, and the next install picks up from there.
- The Hub has a new read, `GET /api/hub/model-downloads`, that lists every download on this Mac, newest first. It needs the owner's token; the Fleet token cannot read it.
- The list shows this Mac's downloads only. A controller does not list its nodes' downloads; the Gateway's fleet install page shows those.

## 0.6.159 — 2026-10-08 · Image job time limits follow step count and Mac speed

### Image jobs get the time they need

An image job's time limit now grows with its number of steps and with how fast the Mac running it is, up to 2 hours. A 50-step Qwen image on a slower Mac is no longer stopped halfway. Short jobs are still given at least as long as before.

Qwen Image 2.1 Low-RAM has now been measured on a real 16 GB Mac: about 8 to 9 minutes for a 16-step image, with memory to spare.

### Technical notes

- Each image job now gets its own time limit: 120 s plus 1.5 × steps × how long a step takes on the Mac running it, between 15 minutes and 2 hours. More steps or a slower Mac get more time; a quick job on a fast Mac is still caught in 15 minutes.
- "How long a step takes" is the Mac's own median from its last 20 finished jobs of that model (3 or more). Without that history it is the stored measurement for that chip (for example Qwen Image 2.1 Low-RAM: 13.3 s on an M2 Max, 33.6 s on an M4), and for a chip nobody has measured, such as an M1, twice the model's slowest measurement.
- The Mac running the job states the limit on the job. The Hub shows it on the batch item, the regular Qwen engine stops at it, and Gateway 0.2.147 waits for it. Gateway still waits at least 30 minutes for every image job, as before.
- Qwen Image 2.1 Low-RAM now has a real 16 GB receipt: the owner's 16 GiB M4 finished four 16-step cases in 463–538 s, peak 6.0 GB, starting with 9.3–10.6 GB free, no swap. The tier, the 8.0 GB start floor and the published notes cite it.
- The model benchmarks page no longer points at the SSD test runners, which were retired today.

## 0.6.158 — 2026-10-08 · Qwen Image 2.1 Low-RAM is the fleet's Qwen; Qwen Image 2.1 runs on 24 GB

### Qwen Image on more Macs

Qwen Image 2.1 Low-RAM is now the fleet's Qwen image model, on every Mac with 16 GB or more. The regular Qwen Image 2.1 now runs on 24 GB Macs too; it stays on the SSD and can be installed on a Mac when you ask for it from the Gateway.

Long image jobs (many steps) are no longer stopped after 15 minutes; the limit is now 30 minutes.

This release also brings 0.6.155–0.6.157 to every Mac: Gemma 4 on 32 GB Macs, choosing which work each Mac accepts, and ranked job priorities.

### Technical notes

- Qwen Image 2.1 Low-RAM is now the fleet's Qwen (16 GB and up); the regular Qwen Image 2.1 is qualified on 24 GB and up and can be installed on request.
- Regular Qwen Image 2.1 (MLX affine 4-bit, native mlx-serve) is qualified on 24 GB from the owner's physical 24 GiB M4 run: four 16-step cases in 440–500 s, peak 9.8 GB, normal memory pressure, no swap. It stays on the SSD and is not copied to Macs by the model copy step; install it from the Gateway when wanted.
- Qwen Image 2.1 Low-RAM (MLX 4-bit) is qualified on 16 GB and up by the owner's decision. Its evidence is the 32 GiB run (peak 5.85 GB); no 16 GB run is recorded. 8 GB is not qualified. The copy step now puts it on every 16 GB and larger Mac.
- A Qwen job starts when the Mac has its measured peak plus 2 GB free: 12.5 GB for regular, 8.0 GB for Low-RAM. Idle models on that Mac are released first. Gemma 4 26B stays 32 GB only.
- Image jobs may now run up to 30 minutes (was 15) before the Hub stops waiting, and a regular Qwen job's own limit grows with its step count, so a 40- or 50-step job on a 24 GB Mac is no longer cut off.
- This is the first fleet release of 0.6.155–0.6.157: Gemma 4 qualification on 32 GiB, per-Mac work choices, and ranked job priorities.

## 0.6.157 — 2026-10-08 · Ranked work priorities on each Mac

### Choose what this Mac runs first

In **Settings > Work on this Mac > Job priority**, choose **Custom order** and use the arrows to rank enabled functions. Choose **No preference** to give all enabled functions equal priority.

The current job finishes first. The highest-ranked eligible queued work runs next, with older work first at the same rank. Other enabled functions use free time when higher-ranked work cannot run. Continuous higher-priority demand can keep lower-ranked jobs waiting.

Your existing LLM-first setting is kept. Each Mac has its own choices; changing priorities keeps pauses and enabled functions in place. Gateway can edit an updated Mac's priorities from its Fleet machine menu.

### Technical notes

- Settings > Work on this Mac replaces Prefer LLM jobs with No preference or Custom order. Move enabled functions up or down using accessible buttons; saved disabled-function ranks are retained. Existing LLM-first settings migrate with other functions tied.
- Finish current work, then choose the highest-ranked eligible queued function; equal ranks use oldest eligible work first. Disabled, unqualified or memory-blocked preferred work does not reserve otherwise usable capacity. Existing render precedence and physical execution leases remain enforced.
- Each Mac publishes its order and supports authenticated Gateway controls through its controller. Changing priority never changes enabled functions or manual pauses.
- Bounded synchronous Chat waiting is independent of rank, including No preference. Cancellation and uncertain-completion recovery retain their existing safeguards.

## 0.6.156 — 2026-10-08 · Per-Mac work choices and LLM priority

### Work on this Mac

Settings lists the functions registered on this Mac, so you can choose which work it accepts. Changing a function or priority keeps existing local, Gateway and fleet pauses in place.

Turn on **Prefer LLM jobs** to finish the current job, then run queued LLM jobs before starting new TTS or image jobs. When no LLM jobs are waiting, this Mac takes other enabled work. Other Macs keep their own choices.

### Image

JPEG results can be previewed and saved alongside PNG results, with the correct file format for older results too.

A preview error stays on the Image page, where you can retry it. A new result or successful retry clears that preview error.

### Technical notes

- Choose which registered functions this Mac accepts in Settings > Work on this Mac. Existing local, Gateway and fleet pauses stay in place; other Macs keep their choices.
- Prefer LLM jobs finishes current work, then runs eligible queued LLM work before new TTS or image jobs. Other enabled work uses idle time when no LLM is waiting.
- Gateway Chat can wait within the Hub's bounded allowance. Cancelled waiting requests cannot start later, and uncertain completions stay held for recovery instead of being submitted again.
- Preview and save verified JPEG alongside PNG, with the correct export format for older results too. Image preview errors stay on Image with a retry action and clear after a new result or successful retry.

## 0.6.155 — 2026-10-07 · Gemma 4 qualification for the 32 GiB Mac

### Chat

**Gemma 4 26B-A4B QAT MLX 4-bit is qualified for the 32 GB Mac.** It completed real English chapter and scene-prompt tests, including a 50-scene batch. The owner accepted the generated chapter lengths; exact 1,200-word chapters are not promised.

This deployment uses a 32k combined context budget. The 24 GB tier remains unqualified. Qwen3.5-9B stays the default, and the other new candidates remain unavailable until qualified.

Qualification information now belongs to the selected model, so Gemma shows its own evidence.

### Technical notes

- Qualify the pinned Gemma 4 26B-A4B QAT MLX 4-bit checkpoint on the measured 32 GiB M2 Max only. The owner accepted the observed 985/800/805-word chapters and Story Studio's verified numeric parsing of Chapter 8/08. The original strict 1,200-word test remains recorded as failed; no lower RAM tier is qualified.
- Use a curated 32,768-token combined input/reserved-output budget on this deployment. Keep the model's stated 262,144-token architecture separate, and the existing 16,384-token output allowance labelled as a worker setting rather than a measured 16k completion.
- Retain exact publisher revisions/checksums and two-run evidence, including the successful 50-scene response under the 4,000-character cap. Qwen3.5-9B stays the default; Nemotron and Qwen3.6 remain pending.
- Chat catalog qualification now reads the selected model's evidence instead of attributing the older Qwen proof to every allowed model. The local installer can preserve the previous app in the project's `_trash` using its archive-path option.

## 0.6.154 — 2026-10-07 · Restart recovery keeps a valid execution until its result is durable

### Jobs

If a restarted job's original result cannot be saved, its replacement keeps running.
When both finish, the first result saved successfully is kept.

### Connections and updates

API key is now called Gateway API Key; the release signing key is now the App Update Key

App updates and controller trust use the App Update Key fingerprint so it is easier to
recognize the key used to verify updates. Existing connections and trusted keys keep working.

### Technical notes

- An abandoned original finalizes on a private candidate. The broker rechecks the competing
  result and cancellation, durably saves the winning batch, then marks the replacement
  superseded and requests its cancellation. Metadata or ledger failure leaves the replacement
  execution intact; a later result cannot overwrite the first finalized winner.
- Shared finalization keeps artifact metadata private across awaits and checks ownership before
  recording an asset. Receipt identity, hash verification and callback reservation remain enforced.
- Controller trust, update notices and App updates use App Update Key display labels. Identifiers,
  endpoints, persisted fields and signature/trust behavior are unchanged.

API key is now called Gateway API Key; the release signing key is now the App Update Key

Focused fault/race regression: five cases failed on the 0.6.152 base, then the 30-case free-work
reroute suite passed after repair. The expanded 34-case acceptance suite and a 28-test replay of
the previously failing paths passed before the 0.6.153 Chat changes were merged. Complete combined
0.6.154 source gates, native build, dark/light fixture review, packaging and installation remain.

## 0.6.153 — 2026-10-07 · Chat model selection and measured processing progress

### Chat

**See when a model is loading, processing your prompt, or generating its answer.** Prompt processing and answer generation show their own measured token rates. Switching models releases the previous model first, and repair attempts keep their own measurements.

**Three MLX 4-bit candidates are prepared for testing:** Nemotron 3.5 Lightning 30B-A3B, Qwen3.6 35B-A3B, and Gemma 4 26B-A4B QAT. They stay unavailable for ordinary generation until qualification; no RAM tier or performance result is claimed yet. Qwen3.5-9B MLX 4-bit remains the default.

JSON streams wait for validation and repair before returning output. A failed load clears the running status, and incomplete replies retain the model's actual finish reason.

### Technical notes

- Chat reads every immutable `chat-mlx` manifest, preserving the Qwen3.5-9B MLX 4-bit default and safe single-model legacy catalogs. One global session lock owns one resident model; switching releases the old model before loading another, and failure/cancellation returns the session safely.
- Nemotron 3.5 Lightning 30B-A3B MLX 4-bit, Qwen3.6 35B-A3B MLX 4-bit, and Gemma 4 26B-A4B QAT MLX 4-bit have reproducible pinned runtime manifests. All three carry `production_allowed:false`, unqualified status and unknown RAM/tested-context values. No inference, throughput, quality or physical tier claim is made.
- Explicit pending admission is enforced at the Hub, peer and worker boundaries. Legacy manifests retain their established policy. Normal kit model selection skips pending packages and preserves existing stock; a parent cannot pull a pending dependency into ordinary installation.
- Chat reports request-scoped Waiting, Loading, Processing prompt and Generating states, exact MLX prompt/decode statistics and separate repair attempts. Hub history preserves closed, finite telemetry; the native app displays worker-stated rates and labels completion time separately. Unknown counts stay null and actual zero remains zero.
- Native Chat polls an owner-authenticated request-scoped activity route. Preflight errors close activity; queued requests do not claim to be loading. Structured JSON streams buffer until validation and one repair have finished, and invalid output fails before HTTP200. Actual final finish reasons are preserved.
- Staging copies the tracked shared cache helper to Chat's package import path as well as the existing Voice layout; missing/untracked helpers and unresolved imports still refuse the build.
- Chat reuses the configured shared MLX cache limit of 512,000,000 bytes before model loading; the standalone qualification probe uses the same setting. This is configuration, not measured memory eligibility.
- Offline proofs use fake engines, tokenizer responses and synthetic preserved fixtures. Physical inference remains held until the owner closes other work, restarts and explicitly starts one model at a time. Candidate stock/hash receipts and qualification tooling are retained in `_orchestra/audit-20261007/moe-qualification/` outside the checkout.

## 0.6.152 — 2026-10-07 · Updates no longer stall while a release is still arriving

### Updates

**Updates no longer stall when a Mac checks for an update while it is still arriving; a failed
automatic update can be retried remotely.** A Mac that looks for the new version while its files are
still being delivered now waits for them, and tries again later, instead of giving up. If an automatic
update does fail, the Mac tries it again on its own after half an hour, or at once when the update is
sent again, and the Mac's last update problem is reported to the fleet so you can see which Mac failed
and why.

### Technical notes

Branch `fix/release-stage-race` off 0.6.151 2e38cbe. Found in the 0.6.151 rollout: a Gateway delta push stores the
manifest (advertising the release) ~110 s before its ~33 blobs. A controller's app that checked inside that window got
`missing_blob` from the stage, fell back to the disk image (404: a manifest-pushed controller holds none), set
`autoUpdateFailedBuild`, and never retried until quit; its nodes, which mirror only what their controller runs, stalled too.
- **Controller stage waits for its blobs** (`release_sync._await_blobs`): while `app_release.missing_blobs()` is non-empty
  (and no disk image is held) the stage reports `phase: downloading` with byte progress, up to `BLOB_WAIT_S` (15 min); still
  missing → `state: "not_ready"` (retryable), never `fallback`. A re-push during the wait ends it as `not_ready` too.
- **Node blob fetch keeps asking** (`release_sync._remote_blob`): a controller that cannot supply a blob (still receiving
  it, or restarting into its own update) is asked again every `BLOB_WAIT_POLL_S` for up to `BLOB_WAIT_S`, then `not_ready`
  instead of the disk-image relay a node cannot use.
- **App** (`StudioAPI.installOfferedRelease`, both roles): `not_ready` is not a failure (no image download, no failure
  memory, the drain ends, the next tick asks again). An automatic failure is remembered by descriptor **signature** (a re-push
  is tried at once) and retried after `autoUpdateFailureRetry` (30 min) instead of never.
- **Fleet report**: the app sends `last_update_error {phase, reason, build, at}` (phase `verifying|staging|downloading|
  installing`, one-line reason) with `POST /api/hub/startup/sync`; `startup_control` keeps it cleaned and publishes it in
  the `startup` object (resources, node rows, roster). Gateway shows it once it adds the key to `fleet._STARTUP_KEYS`.
- Tests: `tests/hub_regression/cases/test_release_delta_routes.py` (controller waits then builds; not-ready timeout;
  node keeps asking then builds; node not-ready), `tests/test_remote_startup.py` (report cleaned; node row carries it),
  `tests/native/api_main.swift` (retry after 30 min and on re-push for node and controller roles; not_ready case; report).

## 0.6.151 — 2026-10-07 · Breeze narration speaks one sentence per section

### Voice

**Breeze narration speaks one sentence at a time with even pauses — fewer bad takes and steadier
pacing.** Each sentence is read on its own and joined with 1 s after a sentence and 1.5 s at a
paragraph break. A very short sentence ("Let's get cosy.") is read together with the sentence next
to it in its paragraph, and a very long one is still divided at a natural break.

### Technical notes

Branch `feat/breeze-sentence-sections` off 0.6.150 fa79767 (keeps 0.6.150's Qwen Image 2.1 backends and DMG packaging).
In 7 days of fleet traffic Breeze rejected and re-rendered 13.6% of section attempts (about 25% of its machine time), rising
with the sentences in a section (1: 6.2%, 2: 15.3%, 3: 23.2%, 4: 25.4%): Breeze pauses at a sentence break and does not come
back. The owner listened to the A/B (`_orchestra/audit-20261001/breeze-ab/`) and chose one sentence per section.
- **`long_form_policy` / `catalog.Family` gain `max_sentences_per_section`**: Breeze 1, every other family None (unchanged).
  `_sentence_safe_text_chunks(..., max_sentences=)` honours it; the 250-character cap still splits an over-long sentence.
- **Tiny sentences**: with a sentence cap, a sentence under 30 spoken characters (`_SENTENCE_SECTION_MIN_SPOKEN_CHARACTERS`)
  joins a neighbour in its own paragraph (forward when it opens the paragraph), so the 1.5 s paragraph pause stays put. 30 is
  about 6 words, where the text check (max(2 words, 15%) unheard) can still reject a section; 14-day fleet Breeze sections
  of 15-39 characters were 0/19 rejected, and 2.3% of real Breeze sentences are under 30. Other families keep 12.
- Joins are unchanged and text-led: 1.0 s between sentences, 1.5 s at a paragraph, a request's own pauses win. The section
  timeline (Story's script-timed subtitles) gets one row per sentence; a 1,500-character Story request is ~15 sections.
- **Per-section Whisper cost**: with no `language` asked, Whisper auto-detected per section, which runs its encoder twice.
  A section after an accepted one is now read in the language Whisper detected there (`GenerationJob.section_language`,
  in memory only); a re-render detects again. Khmer sections keep their forced `km`.
- Tests: `components/voice/app/tests/test_breeze_sentence_sections.py` (one sentence per section for Breeze only, tiny merge
  in-paragraph and at 30, long-sentence split, pauses at joins, timeline rows, Story-size request, language reuse/retry).

## 0.6.150 — 2026-10-06 · Qwen Image 2.1 packages and physical SSD qualification

### Images

**Qwen Image 2.1 4-bit is available as a regular image model.** Choose it wherever you choose an
image model. The normal package currently requires 32 GB and macOS 26.2 or later. A separate
low-memory package is included for qualification; the SSD carries physical 24 GB normal and
16 GB low-memory test commands. Lower RAM tiers become available after those receipts are reviewed.

**Switching image models restores the normal cache settings, and restarting an Image worker stops
its native image process before another worker starts.**

### SSD kits

**Interrupted model copies and replaced model revisions are preserved in `_trash`.** The Qwen test
commands verify the packaged runtime and record memory pressure, headroom, process footprint and
peak swap growth. Missing measurements cannot count as a pass.

### Technical notes

- Adds ordinary selectable Qwen Image 2.1 affine 4-bit packages: pinned mlx-serve 26.10.1 native
  backend and separate mflux q4 low-memory backend. RAM admission remains conservative at 32 GB
  while physical 24/16 GB SSD receipts are pending. Only the native package requires macOS 26.2;
  the existing Image models retain macOS 14 support. No language or thumbnail-only restriction.
- The native leaf is private, loopback-only, bounded, cancelled and reaped. Hub restarts clean the
  worker's verified isolated process group even after leader exit. Low-Qwen restores cache settings
  after success/failure and correctly reports ignored guidance when no negative prompt is allowed.
- SSD commands check exact runtime inventory and content digest, clone only the candidate package
  into private state, refuse overlapping workers, and require complete footprint/pressure/swap/
  admission-headroom measurements before a candidate pass. Peak swap growth determines pressure.
- Model updates archive interrupted copies, replaced folders and old revisions rather than deleting
  them. Failed app stages and native diagnostics remain recoverable; the tracked DMG builder
  preserves previous installers and staging.
- Focused regression checks cover lifecycle, cache restoration, request contract, package hashes,
  qualification fail-closed behavior and model-copy recovery. Packaged and release verification
  records are retained under `_orchestra/audit-20261006/qwen-release-0150`.

## 0.6.149 — 2026-10-05 · A Hub restart re-runs the work it interrupted instead of parking it for recovery

### Jobs

**When a Mac's Studio Hub restarts in the middle of a job, the job now runs again on a free Mac by itself instead of needing recovery. If the original Mac had already finished it, that result is used. The Macs it was using are free again straight away.**

### Technical notes

Branch `fix/restart-lost-free-work` off 0.6.148 a7fd296. On 2026-10-05 22:14:56 the Terranash-1600 controller's Hub process restarted
(the app itself did not stop, so `last_stop` stayed at its 18:33 update). `restore_batches` fenced its five running Z-Image items
`uncertain` although their nodes had finished all five (22:12:56-22:15:42): the Gateway parked the jobs as "Needs recovery"
(UNKNOWN_OUTCOME) and `busy_machines()` held those five idle Macs until the 30-minute uncertain sweep, while new work queued.
- **Running free work is re-run after a Hub restart, on any capable Mac (owner, 2026-10-05; extends the 2026-10-03 worker/node rule).**
  `broker._rerun_after_restart` requeues every running Image, Music, Video and Voice item of a batch that is not being cancelled -
  this Mac's own worker or a node alike - and no Mac is held for it. Voice takes a fresh client request id, as `_abandon_attempt` does.
  The original is watched once its studio is back in the registry (`_watch_restored_attempts` -> `_watch_abandoned_attempt`, from the
  dispatch loop): a finished original is adopted (the re-run, if one started, is cancelled); one still running is told to stop; one that
  finishes after the re-run is ignored. The interrupted try stays spent: on its last of MAX_TRIES a restart ends the item `error` /
  `HUB_RESTARTED` (published, so the Gateway reads a known failure) instead of a fence.
- **Transcription likewise**: a packaged Hub re-queues a running Subtitle item after a restart (it was fenced `uncertain` with
  `manual_action_required`); on its last try it ends `error` / `HUB_RESTARTED`.
- Unchanged: an item whose batch is being cancelled keeps the cautious fence; cloud work never reaches the Hub (refused at the door).
- Tests: `hub_regression/cases/test_free_work_reroute.py` (restart re-runs every modality on this Mac and on a node and holds no Mac;
  a finished original is adopted with no POST; original-first and re-run-first both settle on the first result; two restarts in a
  row re-run, a third ends `HUB_RESTARTED`; a cancel in flight stays fenced); `test_voice_durability` (the original voice job is adopted
  by its own request id after a restart); the restart expectations in `test_image_uncertainty`, `test_music_durability`,
  `test_hub_jobs`/`_hub_dispatch_proof` (Voice, Subtitle) and `test_startup_recovery` now expect a re-run.

## 0.6.148 — 2026-10-05 · Why a LAN copy fell back is recorded; an update no longer marks finished downloads interrupted

### Models

**Copying a model between Macs now records why a copy fell back to the internet; finished downloads are no longer marked interrupted by an update.**

### Technical notes

Branch `fix/lan-share-followups` off 0.6.147 85a1344 (findings: `_orchestra/audit-20261001/lan-share/ROLLOUT-20261005.md`, Hub 4 and 5).
- **Copying a model between Macs now records why a copy fell back to the internet.** `model_packages._download_file` swallowed the peer's
  error when it dropped the peer (both Shops Lite iMacs fell back on 2026-10-05 with no reason anywhere). It now prints one line the moment
  it happens - `LAN copy failed, using the internet (internet|mixed): <ErrorType>: <reason>` (share URL masked) - and
  `model_downloads._public` reads it, so a LAN run shows `lan_error` and `source_used: "internet"`/`"mixed"` **while it is still running**
  (a finished run keeps the `source_used` of its closing JSON). Gateway 0.2.126 uses it to relabel the row and count it as the network's
  one internet download. `_source_used` reads the JSON after that line.
- **Finished downloads are no longer marked interrupted by an update.** `model_downloads.shutdown()` marked every tracked run `interrupted`
  with the stop's exit code, including runs whose downloader had already exited 0 (1301/1302 read `interrupted, exit_code 0` with the model
  cached). A child that has already exited is now recorded as it ended (`done`/`error`, with `source_used`); only live ones are stopped.
  `_stop_owned`'s final group SIGKILL also tolerates macOS's EPERM for a group holding only a zombie (it raised out of `shutdown()`,
  leaving later runs unstopped; seen 1 in 6 runs of the lifecycle test once a downloader child had started).
- Tests: `test_model_downloads.py` (a finished run stays `done` through shutdown), `test_model_packages.py`
  (`test_why_a_copy_fell_back_reaches_the_running_run`).

## 0.6.147 — 2026-10-05 · Phonon-2 loads its real weights; a test transcription on one chosen Mac

### Models

**Phonon-2 (optional English transcription) now loads its real weights correctly; still in testing, Whisper stays the default.**

### Technical notes

Branch `fix/phonon2-nzrow` off 0.6.146 a58475d.
- **Phonon-2 (optional English transcription) now loads its real weights correctly; still in testing, Whisper stays the default.**
  `transcription._phonon2_dense` read `<block>.nz_row` as each row's nonzero count; the published `phonon-2-mlx` file stores each row's
  start offset in the hi-bit stream (exclusive prefix sum), so every real block was refused ("trit codes disagree with its nonzero table")
  and no Phonon-2 transcription could start on any Mac. The check now compares `cumsum(counts) - counts` (also right for an empty
  matrix). With it, all 697 tensors match the publisher bit for bit (`_orchestra/audit-20261001/phonon2/RESULTS.md`, G0.2). The test
  fixture `_pack_five_value` now emits the shipped form, and a per-row-count `nz_row` is asserted refused. The Subtitle worker loads
  through the same function (`engine.py` -> `load_phonon2`), so it has no second copy to fix.
- **Admin test transcription on one chosen Mac (model qualification).** `POST /api/hub/fleet/nodes/{id|local}/transcribe` (Fleet token,
  multipart `file`, `model`, `language`, `word_timestamps`): on this Mac it is the peer surface's own
  `/studio/transcription/api/transcribe` (same admission, machine reservation and worker re-signing); for a joined node it is relayed to the
  node's own `.../nodes/local/transcribe` by `fleet_nodes.relay_model` (the 0.6.143 install relay, now able to carry a raw body, its own
  timeout of 960 s and its own "needs 0.6.147" refusal). Audited `transcribe_relay` / `transcribe`. Not in `NODE_ALLOWED`.
- **Subtitle's synchronous `/api/transcribe` keeps each segment's `words`** when `word_timestamps` is on (they were measured and then
  dropped); with it off the segment shape is unchanged. Contract: `_orchestra/audit-20261001/INTERFACES-0147.md` (Gateway 0.2.125).
- Tests: `test_transcription_engines.py`, `tests/hub_regression/cases/test_fleet_model_install.py`, `test_subtitle_standalone.py`.

## 0.6.146 — 2026-10-05 · Macs copy models from each other; Phonon-2 to try; narration timeline; longer narration deadline

### Models

**Macs on the same network can copy a model from each other instead of each downloading it from the internet (the Gateway
picks a nearby Mac; every file is checked).**

**Phonon-2, a small fast English transcription model, is available to try next to Whisper.**

### Narration

**Narration's text check allows a little more mishearing in short sections (a skipped phrase is still caught).**

**Narration now reports where each piece of text sits in the audio, so subtitles can be timed exactly from the script.**

**Long narration pieces get 45 minutes before they're stopped.**

### Technical notes

Release of `release/0.6.146` (= 0.6.145 76a935b + `--no-ff` merges of `feat/lan-model-share` 5aac7dd, `feat/phonon2-asr` b1abff5 and
`fix/breeze-failures` 6bf5646, then `feat/section-timeline` 8b9b0a6). No textual conflicts.
- **Macs on the same network can copy a model from each other instead of each downloading it from the internet (the Gateway picks a
  nearby Mac; every file is checked).** `POST /api/hub/fleet/nodes/{id|local}/model-packages/{pkg}/share` (Fleet token, relayed like the
  0.6.143 install) returns a 30-minute, renewed-on-use, read-only token for the one verified revision; `GET/HEAD
  /api/hub/model-share/{token}/files/{path}` serves only the manifest's files (Range, octet-stream, no gzip, <= 2 transfers per Mac,
  429 beyond, audited). The download route takes `{"source": share}`: each file is tried from the peer, verified against the pinned
  digests exactly as for the internet, and re-fetched from the internet on a mismatch; a peer that fails is dropped for the run. The run
  records `source_used` (`lan` / `internet` / `mixed`). Contract: `_orchestra/audit-20261001/INTERFACES-0146.md`.
- **Phonon-2, a small fast English transcription model, is available to try next to Whisper.** Package `phonon-2-mlx`
  (selcukkubur/Phonon-2-mlx@71936ba3, 5 files, 189,498,804 bytes, unqualified, 8 GB tier, provisional 1.5 GB free floor);
  `transcription.load_phonon2` unpacks it into mlx-audio's ParakeetTDT with numpy (no new dependency). Voice `phonon2` engine and the
  Subtitle `phonon2` family; non-English requests are refused naming Whisper. NeMo-family word timestamps join SentencePiece pieces into
  words. Whisper large-v3 turbo stays the default; the Lite Mode allowlist is unchanged. Catalog count 29.
- **Narration's text check allows a little more mishearing in short sections (a skipped phrase is still caught).** `tts_quality` lets 15% of a
  section's words go unheard (was 10%; measured good >= 0.938, bad <= 0.849); the dropped-clause rule (missing run > 3) is unchanged.
- **Narration now reports where each piece of text sits in the audio, so subtitles can be timed exactly from the script.**
  `section_quality.timeline = [{section, start_s, end_s, text_start, text_end}]`: speech start/end of each section in the joined file
  (tempo-adjusted) and its character span in the request text; the broker passes it through numbers-only.
- **Long narration pieces get 45 minutes before they're stopped.** Voice job deadline floor 2,700 s (was 1,800 s; slowest successful
  1,500-character Breeze part took 1,747 s on an M1 16 GB). Stall detection is unchanged.
- Tests: `tests/hub_regression/cases/test_lan_model_share.py`, `tests/test_model_packages.py`, `test_transcription_engines.py`,
  `test_model_manifest_catalog.py`, `test_subtitle_standalone.py`, `test_tts_quality.py`, `test_hub_job_deadline.py`,
  `test_section_quality_passthrough.py`, `test_long_form_join_edges.py`. `docs/model-benchmarks.md` regenerated for Phonon-2.

## 0.6.145 — 2026-10-04 · Breeze TTS 2 4-bit runs on 8 GB Macs

### Voices

**Breeze (the English voice model) now also runs on 8 GB Macs — measured on an 8 GB M1: it fits with room to spare, at
the same speed as a 16 GB M1 — so English narration can use the whole fleet.** "2 - Copy the models" on the kit now puts
Breeze on 8 GB Macs too, and "Install a model" in the Gateway offers it for them.

### Technical notes

Release of `release/0.6.145` (= 0.6.144 d14ad78 + the owner's Breeze decision).
- **Breeze TTS 2 4-bit: 8 GB total-memory floor, 3.2 GB free to start** (were 16 GB / 5.6 GB; owner decision 2026-10-04). Evidence:
  `_orchestra/audit-20261001/breeze-8gb/results/TTS 8GB Run 20261004-230549` - 8 GB M1, Hub 0.6.143, 2/2 S2 clips completed, all sections
  accepted, each clip started with 3.31-3.46 GB free, worker peak 4.69 GB, Mac max used 5.2-5.3 GB, min free 1.19-1.32 GB, pressure warning
  (never critical), swap <= +0.54 GB, RTF 4.1, max stall 0.6 s. One source: `memory_admission._FLEET_DEFAULTS` (measured rung of
  `requirement_gb`), so dispatch, the published tier / `memory_eligible` (`publish_floor`), the fleet install refusal (`_memory_refusal`)
  and `docs/model-benchmarks.md` all read 8 GB. The manifest is unchanged (digest-pinned), as for FLUX.2 Klein.
- **The Voice worker's own preflight takes the same 3.2 GB**: `memory_admission.worker_free_floor_gb` (was `override_free_gb`) returns
  the operator override, else the fleet measurement, and the broker forwards it as `memory_floor_gb`. Without it the worker's size
  estimate (2.9 + 1.25 = 4.15 GB cold) would have refused every job an 8 GB Mac was admitted for. Models with no measurement keep the
  worker's estimate. The Hub's critical-pressure safety net and the INSUFFICIENT_MEMORY hand-back are unchanged.
- Voice catalog row: `min_unified_memory_gb` 8, `min_free_memory_gb` 3.2, note / recommended hardware / use cases from the measurement.
- Kit: `copy_models_for_this_mac.PROVEN_SMALLER["breeze-tts-2-mlx-4bit"] = 8`, so "2 - Copy the models" carries it to 8 GB Macs. Not on
  the Lite Mode allowlist (`components/workstation_models.py`). The 8 GB Breeze test's stale "16 GB only" sentences are updated.
- Tests: `test_breeze_4bit_runs_on_8gb_macs_from_its_measurement` (hub_regression), `test_breeze_is_copied_to_8_gb_macs_on_its_measurement`.

## 0.6.144 — 2026-10-04 · Longer narration pauses; a standing-by Lite Mode Mac is not free capacity

### Narration

**Narration pauses are longer and more natural: 1 second after each sentence and 1.5 seconds between paragraphs, where
the text is joined.** This is for every voice model. A short pause inside a very long sentence stays short. A request that
asks for its own pauses still gets them.

### The fleet

**A shop Mac in use (Lite Mode standing by) no longer counts as free, so work isn't held for it.** As soon as its user
steps away it counts again.

**A model installed from the Gateway or the Model library shows as installed straight away**, instead of reading "not
installed" for up to a minute after the download finished.

### The kit

**RUN-8GB-BREEZE test in the kit's Technical folder.** It checks Breeze TTS 2 on an 8 GB Mac.

### Technical notes

Release of `release/0.6.144` (= 0.6.143 a0f8263 + `--no-ff` merges of `feat/lite-capacity` 9d13187, `feat/kit-breeze-8gb-test` d41f1dc and
`fix/install-model-visible` 38402e5, and the owner's pause decision). No textual conflicts.
- **Narration pauses are longer and more natural: 1 second after each sentence and 1.5 seconds between paragraphs, where the text is joined**
  (owner decision 2026-10-04). `long_form_policy.UNIFORM_SENTENCE_JOIN_PAUSE_SECONDS` / `UNIFORM_PARAGRAPH_JOIN_PAUSE_SECONDS` = 1.00 / 1.50 s (were
  0.60 / 1.00); the mid-sentence soft join stays 0.15 s. They are the one source for the joiner (`_production_join_pauses_s`), the published
  `join_pause_milliseconds` (now 1000), the lane contract's `sentence_pause_sec` / `paragraph_pause_sec` defaults and the section silence check
  (`tts_quality.max_pause_s`: 4.5 s after a sentence, 5.0 s at a paragraph). Request bounds stay 0-3 s; a request that sends its own pause keeps it.
  Tests pinning 0.6 / 1.0 now pin 1.0 / 1.5; README, `docs/model-benchmarks.md` (+ `qualification/model_record.py`) and the catalog notes follow.
- **A shop Mac in use (Lite Mode standing by) no longer counts as free, so work isn't held for it** (`capabilities._model_capability`: a worker with
  `standing_by` - standing by, stepped aside or quiet hours - is not `capacity_eligible`; `build_snapshot` carries `standing_by`). It re-enters the
  eligible totals as soon as it is free. Test: `hub_regression/cases/test_capability_supply.py`.
- **An installed model shows as installed the moment its download says done** (`StudioMonitor.refresh_machine_catalogs`): a status read that
  reports a run `done` re-reads that Mac's worker catalogs first, once per run (`/api/hub/model-downloads/{id}`, the fleet route for this Mac, and a
  relayed node read). Live 2026-10-04: Fish S2 Pro 4-bit finished (`done`, exit 0) but the Fleet still said `cache_state` `absent` until the 60 s
  catalog refresh. Tests: `hub_regression/cases/test_fleet_model_install.py`.
- **Kit: 8 GB Breeze test** (`qualification/RUN-8GB-BREEZE.command`): refuses on a non-8 GB Mac, imports `breeze-tts-2-mlx-4bit` from the kit through
  `import_kit_packages.py`, then runs `eight-gb-tts-run.sh --models breeze` (calibrated 0.6.143 sampling sent explicitly, 2.5x clip limit). A clip the Hub
  kept queued for the whole limit is reported "did not start / NOT MEASURED", not "timed out". Copied by `prepare-kit-0144.sh` (MANAGED list too).

## 0.6.143 — 2026-10-04 · Calibrated voice defaults; faster fleet reads; no tiny sections; a group takes a job any of its Macs can run; install a model from the Gateway

### Steadier voices

**Each voice model now starts from settings chosen by listening.** The fleet rendered every model with several settings
and five takes each, and each model now uses the steadiest one. Breeze TTS 2 is the big change: in your voice its takes
vary half as much, and sections that had to be redone or failed dropped from 22% to 7%. Fish S2 Pro, Qwen3-TTS, VoxCPM2
4-bit, OmniVoice, Pocket TTS and the Khmer voice are steadier too. VoxCPM2 8-bit and the other models keep what they had.

**No more failures from a "***" line or a tiny scrap of text.** A scene break such as "***", or a section with almost
nothing to say, used to be read on its own and came out silent on every Mac. Now a line with nothing to speak is left out
(it still gets its pause), and a very short piece joins the one next to it.

**Honest messages when a voice writes no audio.** It only says memory was short when it really was.

**Khmer narration is no longer turned down by the listening check.** The speech recogniser cannot read Khmer, so its
check meant nothing and rejected good Khmer narration. It is no longer run for Khmer; the silence, length and pause checks
still are. When a section fails every try, the message now gives the measured numbers for each try.

### The fleet

**Faster fleet reads.** The Gateway's view of each group now travels compressed, so the Fleet page fills in seconds
instead of up to 20 seconds over a slow link.

**A group takes a job when any of its Macs can run it.** A group used to turn a model down when only one of its other
Macs had it installed; now the job is accepted and sent to that Mac.

**A model paused because of the text is un-paused.** If a model was paused only because of bad text (such as a "***"
line), the pause is lifted when Studio Hub starts.

**Memory needs match the real limits.** For example VoxCPM2 4-bit now shows as able to run on 8 GB Macs, which it
always could.

**"Failed" counts are honest, and can be reset.** A Mac that turned a job away because it was short of memory (the job
then ran elsewhere) no longer counts it as failed. The Gateway's "Reset counters" now starts the counts afresh, without
deleting anything.

**Install a model on a Mac from the Gateway.** Fleet > Machines can ask a Mac to download a model. A Mac turns down a
model it could never run (too little memory), one that is shelved, or one that does not fit on its disk.

### The kit

**"2 - Copy the Lite Mode models"** sits next to "2 - Copy the models" on the drive. Use it on a Mac that will run Lite
Mode: it copies only the models Lite Mode uses, then you tick Lite Mode in Studio Hub > Settings. The "Set Up a Shop Mac"
and "Set Up the Shops Controller" commands have left the kit (an old copy on a drive is moved to that drive's trash).

### Technical notes

Release of `release/0.6.143` (= 0.6.142 aeb89a5 + `--no-ff` merges of `perf/controller-read` fba7d3c, `feat/kit-lite-models-copy` a36ca55,
`fix/tiny-section` 9231ee5, `fix/0143-small` 8cfc095..3543f56, `fix/machine-counters` 37eb4cd, `feat/node-model-install` fe486fa,
`fix/khmer-gate` 65e49f7, then the Khmer transcribe-back skip and the calibrated sampling defaults). No textual conflicts. Gateway contract for the
model install: `_orchestra/audit-20261001/INTERFACES-0143.md`.
- **Fleet JSON is gzip-compressed** (`standalone.create_app`: Starlette `GZipMiddleware`, level 5, >= 1 KiB). A Gateway's read of one controller was
  ~3.6 MB of JSON over Tailscale at 0.5-1.2 MB/s (4-20 s); it compresses 9-26x. `application/octet-stream` (release images and blobs, read with
  `iter_raw` and resumed with Range) and Starlette's default media exclusions stay identity; a client without `Accept-Encoding` gets identity.
- **Kit: `2 - Copy the Lite Mode models.command`** (`copy-lite-models.command` at the kit root): runs `2 - Copy the models` with
  `STUDIOHUB_WORKSTATION=1`, copying only `components/workstation_models.py`'s models, then says to tick Lite Mode in Settings. It changes neither role
  nor Lite Mode. `Set Up a Shop Mac.command` leaves the kit; `prepare-kit-extras.sh` moves a drive's copy (and the older `Set Up the Shops
  Controller.command`) to `<volume>/_trash/Extras-retired/`. Docs, `release_kit` operator steps and `test_shop_kit` follow.
- **No tiny or unspeakable section reaches a model** (`generation._sentence_safe_text_chunks`): a paragraph with nothing to speak (`***`) is left out
  (its boundary keeps the paragraph pause), a section under 12 spoken characters (CJK counts three) joins its neighbour, and a request with nothing to
  speak is a ValueError up front. Join pauses find sections whitespace-insensitively (`_find_section`). "Produced no audio" for a tiny/unspeakable
  section is a ValueError (the text, not the model); memory is blamed only when free memory was below the model's loaded need.
- **Content-caused pauses are lifted** (`broker._counts_against_model`, one classifier for new failures and stored pauses): on start,
  `_load_model_protection` re-reads `model_protection.json` and lifts a pause whose failure was the text (incl. an old worker's "produced no audio.
  N-character section" with N < 12); engine failures and missing weights stay paused.
- **Published memory tier = admission floor** (`memory_admission.publish_floor`, applied by `monitor._assemble_aggregate_catalog`): VoxCPM2 4-bit
  is memory-eligible on 8 GB Macs (was published 16 GB while dispatch enforced 8 GB); FLUX.2 Klein / Z-Image-Turbo likewise. `qualification/model_record.py`
  reads the same floor; `docs/model-benchmarks.md` regenerated.
- **A group accepts a job when any of its Macs can run it** (`standalone._a_joined_mac_can_run` in `_submission_readiness`): a joined Mac with the model
  cached, runtime-ready, available, not paused, Lite-Mode-allowed and within its RAM floor is enough (group 1500 refused Fish S2 Pro 4-bit with
  `MODEL_NOT_READY` while node 1504 had it). Model-aware dispatch sends it there.
- **Refusals are not failures** (`activity.REFUSAL_CODES`): a Voice MemoryGuard refusal ends typed `INSUFFICIENT_MEMORY` and is neither counted as
  `failed` nor a reason for `needs_attention` (8 GB Macs read 26 done / 192 failed). `GET /api/hub/stats?activity_since=<epoch>` counts from the
  Gateway's "Reset counters" baseline (nothing deleted).
- **Install a model from the Gateway** (`POST /api/hub/fleet/nodes/{node_id}/model-packages/{package_id}/download`,
  `GET /api/hub/fleet/nodes/{node_id}/model-downloads/{run_id}`; controller runs it itself or relays to the node with the Fleet token). Same refusals as
  the Model library download plus "too little memory to run it here"; audited on controller and node. Only these two routes join `FLEET_ALLOWED`.
- **Khmer transcribe-back is skipped** (`fix/khmer-gate` + follow-up): Whisper large-v3 turbo cannot read Khmer (measured: 48 known-good sections score
  syllable overlap median 0.00, max 0.11; broken audio up to 0.42; `_orchestra/audit-20261001/tts-khmer/GATE.md`). `tts_quality.check_section` records a
  Khmer score as `text_check` "advisory" and never rejects on it (`KHMER_MIN_SYLLABLE_OVERLAP` / `KHMER_MAX_MISSING_RUN` removed), and
  `transcribe_back_applies` is False for Khmer as for CJK, so Whisper is not loaded for a Khmer section; silence / length / internal-pause checks still
  enforce. A section that fails every attempt names each attempt's measured value and limit in the job error (`tts_quality.rejection_numbers`, under the
  Hub's 500-character cap); `broker` whitelists `text_check`.
- **Calibrated per-model sampling defaults** (`catalog.SAMPLING_DEFAULTS_BY_REPO`; owner: "use all ★ recommendations"; source
  `_orchestra/audit-20261001/tts-sampling/REPORT.md` + `recommended.json`): Breeze-TTS-2 4-bit B (temperature 0.6, top_p 0.9, top_k 40, repetition
  1.05; Chris's section failures 22 % -> 7 %, spread 13.6 -> 7.0); Fish S2 Pro 4-bit B (0.6 / 0.7 / 30); Qwen3-TTS 1.7B 4-bit D (0.6, 0.9, 40,
  repetition 1.6); Qwen3-TTS 0.6B 8-bit B (0.6, 0.9, 40, 1.5); VoxCPM2 4-bit B (cfg 2.0, 10 steps); OmniVoice bf16 C (position_temperature 0);
  Pocket TTS 4-bit C (temperature 0.3); Khmer MMS B (noise 0.5 / duration noise 0.6). VoxCPM2 8-bit keeps A; models not listed keep their family's
  values. Pinned by `test_sampling_levers.CALIBRATED_0_6_143` (and every calibrated value checked inside its lever's bound).
- Tests: `components/voice/app/tests/test_tiny_sections.py`, `test_priority_mlx_models.py`, `test_khmer_text_handling.py`, `test_tts_quality.py`,
  `test_sampling_levers.py`; `tests/test_activity_counters.py`, `test_shop_kit.py`; `tests/hub_regression/cases/test_group_readiness_any_mac.py`,
  `test_memory_admission.py`, `test_model_failure_protection.py`, `test_fleet_model_install.py`, `test_hub_gap_routes.py`.

## 0.6.142 — 2026-10-04 · Every TTS model's sampling levers are request fields (defaults unchanged); Khmer narration splits, pauses and is checked correctly

### Lite Mode on any Mac

**Lite Mode: tick it in Studio Hub > Settings on any Mac — standalone, controller or node — and that Mac lends only its
idle time.** Lite Mode is the new name for workstation mode, and it can still be set from the Gateway too. The Settings
section shows what the Mac is doing now (working, standing by, stepped aside or quiet hours) and today's counts.
Changes apply straight away, with no restart.

**Lite Mode now covers everything the Mac runs.** That includes its own work, not only work sent by its controller. A
controller in Lite Mode that is being used sends its group's work to the other Macs; a Mac on its own simply waits.

**"Today: N jobs" now counts every job the Mac ran in Lite Mode**, its own work included (it used to count only work sent
by a controller).

**The Shops controller is set up like any other Mac.** It is a normal controller that also makes things; tick Lite Mode on
it if it is also a work Mac. The "Set Up the Shops Controller" command has left the kit (an old copy on a drive is moved
to that drive's trash). "Set Up a Shop Mac" stays as an optional shortcut.

**Opening the app by hand always shows its window.** Opening Studio Hub from Applications or Spotlight now opens the
window, so a Mac whose menu-bar icon is hidden can still get to Settings. Only the reopen after an update stays quiet.

**"Workstation mode" is now called "Lite Mode"** everywhere you see it, including the kit's set-up commands and
checklist.

### Transcription no longer waits at a group whose Macs are all paused

**A transcription sent to a group whose every Mac is paused is turned down straight away**, and the Gateway sends it to
another group, as voice and image jobs already were in 0.6.141. Pausing the last Mac in a group hands its waiting
transcriptions back within a few seconds, at no charge.

### Every voice model's fine-tuning settings can now be set

**Nothing sounds different yet.** This release only makes the settings reachable, so the best value for each voice can be
found by listening. Every setting starts at exactly the value it had before.

**Each voice model now takes its own steadiness settings with a request.** For example temperature (how varied each
take is), top-p and top-k (how adventurous the next sound may be), repetition penalty, and for the step-by-step models
the number of steps and the guidance strength. This covers Qwen3-TTS, Breeze TTS 2, VoxCPM2, OmniVoice, Fish S2 Pro,
Pocket TTS and the Khmer voice. Each setting has a safe range, and a value outside it is brought back inside.

**Each model's starting values live in one list.** When the listening tests pick a better value for a model, changing
that one list changes it everywhere. You can also set a model's own starting value on this Mac in the Models tab.

### Khmer narration: correct sentence pauses, no mid-syllable cuts, quality check now understands Khmer

**Khmer sentences get a proper pause.** A Khmer sentence ends with ។ (or ៕), and the app did not know that, so where a
long Khmer voice-over was joined after a sentence it left the short pause meant for the middle of a sentence. It now
leaves the full sentence pause, and splits a long voice-over at the end of a Khmer sentence first.

**Khmer written without spaces is never cut in the middle of a syllable.** When there is nowhere better to split, the
app now splits between syllables, never in the middle of one.

**The quality check can now check Khmer.** It used to find no words in Khmer text, so it passed every Khmer part without
really checking it. It now compares Khmer syllable by syllable and listens in Khmer, so it can catch a part that leaves
out a large piece of its text. For now it only fails a part that missed a lot, until it has been tried on more Khmer.

**A fixed seed now gives the same Khmer voice-over every time.** A long Khmer voice-over with a fixed seed restarted
the same randomness for every part, and one without a seed recorded a seed it never used. Both now work like the other
voices: one seed for the whole voice-over.

### Technical notes

Release of `release/0.6.142` (= 0.6.141 + the sampling levers and Khmer fixes below + `feat/lite-mode-every-role` 10e60cb (on `feat/workstation-app-settings` 2e0edf2) + `fix/paused-group-chat-transcription` a2b6284, merged --no-ff). Owner, 2026-10-04: "find a balanced default for each of the models" after the TTS
comparison found the Hub passed Breeze TTS 2 no sampling settings. Audit: `_orchestra/audit-20261001/tts-sampling/LEVERS.md`; calibration plan beside it.
- **One table of sampling levers** (`catalog.SAMPLING_LEVERS`, per family: request field -> engine argument, bound, default; per-checkpoint overrides in
  `catalog.SAMPLING_DEFAULTS_BY_REPO`; `catalog.sampling_kwargs` clamps). New request fields: `qwen_temperature|top_p|top_k|repetition_penalty`,
  `breeze_temperature|top_p|top_k|repetition_penalty|cfg_scale`, `pocket_temperature|lsd_decode_steps|eos_threshold`,
  `omnivoice_class_temperature|position_temperature|layer_penalty_factor|t_shift`, `vits_noise_scale|noise_scale_duration` (Khmer MMS). The existing
  VoxCPM2 (`cfg_value`, `inference_timesteps`, `voxcpm_*`), OmniVoice (`omnivoice_num_steps|guidance_scale`) and Fish (`fish_*`) levers moved into the
  table with their bounds unchanged. Chatterbox is unchanged (its levers were already request fields).
- **Defaults equal what each engine received in 0.6.141** (pinned by `test_sampling_levers.EFFECTIVE_IN_0_6_141`): mlx-audio's `generate_audio`
  passes `temperature=0.7` to every model, so Breeze/Qwen/Pocket sampled at 0.7 (not the 0.9 in their signatures); Qwen's ICL clone floors
  repetition penalty at 1.5; Breeze `cfg_scale` 1.0 = off.
- **Published**: `lane_contract.parameters_for` lists each model's levers with `type`/`minimum`/`maximum`/`default`, so the Models tab can store a
  per-Mac default and a scheduler can read the bounds. An omitted lever is not recorded on the job (`main._job_parameters`), so a request recorded by
  0.6.141 still compares equal on a `client_request_id` replay. Older workers ignore the new fields.
- **Seed**: never passed to an engine (Breeze's own `seed` would reseed every section). Khmer MMS now follows the MLX rule (`_qwen_attempt_seed`, seeded
  once, `_native_continue_rng` sections continue): a fixed seed reseeded every section to one state, and an unseeded job recorded a `resolved_seed` the
  audio never used.
- **Khmer text handling** (`_orchestra/audit-20261001/tts-khmer/REPORT.md` findings 1, 2, 4). `generation._QWEN_SENTENCE_ENDINGS` (the one set the
  section splitter and `_production_join_pauses_s` read) gains ។ U+17D4 / ៕ U+17D5, so joins after a Khmer sentence get the 0.6 s sentence pause, not
  0.15 s. `_split_long_text_unit` prefers a cut after ។/៕ (even with no space after it), then space / zero-width space (U+200B) / `,;:`, and the
  last-resort cut goes through `_cluster_boundary` (never before a combining mark, after a virama-class sign such as the Khmer coeng, or beside
  ZWJ/ZWNJ). Quality gate: `qwen_quality._tokens` adds Khmer orthographic syllables (`_KHMER_SYLLABLE`; `_normalized_text` keeps Khmer signs), so
  `tts_quality.heard_words` counts real units (it reported 0/0/1.0); a Khmer section fails `TTS_SECTION_TEXT_MISMATCH` when syllable overlap
  < `KHMER_MIN_SYLLABLE_OVERLAP` 0.5 or a missing run > `KHMER_MAX_MISSING_RUN` 24 syllables (deliberately loose, unmeasured: `ponytail:` note);
  the English rule is untouched. Whisper transcribe-back reads a Khmer section as `km` (`tts_quality.script_language`) whatever the request's
  `language` says (Story sends the voice's own, e.g. `en`). Tests: `components/voice/app/tests/test_khmer_text_handling.py` (spaced and unspaced
  Khmer fixtures; 8 of its 9 fail on 0.6.141 code).
- **Lite Mode in the app's Settings** (`feat/workstation-app-settings`, 2e0edf2): Settings > Lite Mode reads/saves this Mac's workstation settings
  through its own Hub (`GET/POST /api/hub/workstation`; same labels and policy sentence as the Gateway's Fleet sheet; no restart, `workstation.py`
  re-reads per decision); a hand launch opens the window (only an update's reopen stays quiet). User-facing name "Lite Mode" (kit commands, README,
  checklist, copy-models, refusal sentences); code, API paths, JSON keys, `workstation.json` and error codes keep "workstation". Native tests in
  `tests/native/api_main.swift`, `render_main.swift`.
- **Lite Mode governs every job on every role** (`feat/lite-mode-every-role`, 10e60cb): the scheduler treats `"local"` like any Lite Mode Mac
  (`peers.dispatch_ready` / `standing_by` / `workstation_start_free_gb` read this Mac's `workstation.status()`, `peers.lite_mode_allows`); a controller
  standing by routes its group's work to its nodes, otherwise work waits (`governor_note` "Waiting: Lite Mode - ..."); the pressure-yield loop also
  watches `broker.local_running_jobs()`; a handed-back own job is re-queued here (standalone/node) or handed to the Gateway (controller).
- **"Today: N jobs" counts every job this Mac starts in Lite Mode** (`peers.note_dispatch` at the own-queue dispatch points in `broker`,
  `transcription_jobs`, `chat_jobs`; the peer door already counted). Test: `test_lite_mode_every_role.test_todays_count_includes_this_macs_own_queue`.
- **`Set Up the Shops Controller.command` retired** (owner 2026-10-04: the Shops controller is a regular controller that also generates; set it up
  like any Mac, then tick Lite Mode). Moved to `_trash`; `prepare-kit-extras.sh` ships only `Set Up a Shop Mac.command` and moves a drive's old copy
  to `<volume>/_trash/Extras-retired/`; kit README, NODE-SETUP-CHECKLIST, AGENTS.md, `test_shop_kit`, `test_yam_display_install` follow.
- **Transcription honours a fully paused group** (`fix/paused-group-chat-transcription`, a2b6284): `transcription_jobs` create route answers 503
  `NO_AVAILABLE_MACHINE` (after replay/duplicate checks) via `broker.every_capable_mac_paused`, the dispatcher hands queued items back as
  `error`/`NO_AVAILABLE_MACHINE` without a try, items publish `error_code`, a manual retry clears it. Chat needs nothing (no producer route; the peer
  chat route is synchronous and refuses a paused Mac). Test: `tests/hub_regression/cases/test_paused_group_hands_back.py`.
- Gateway: no change needed — `/v1/audio/jobs` forwards `params` and flattens `controls` into the Hub item's params by name (checked in the sandbox).
- Tests: `components/voice/app/tests/test_sampling_levers.py` (new, 118: defaults pinned per family, every lever clamped at both bounds and published,
  engine argument names checked against the installed mlx-audio / transformers, Breeze end-to-end through `_generate_mlx_audio`, seed once / no engine
  seed, Khmer seed + noise scales); `test_priority_mlx_models.py`, `test_model_defaults.py` follow the move to the table.

## 0.6.141 — 2026-10-04 · A paused group hands its work back; TTS on 8 GB Macs admitted once per job, on one reading

### Paused Macs no longer hold on to work

**A group whose Macs are all paused now says no straight away.** If every Mac in a group that could make a job is
paused, the group turns the job down at once and the Gateway sends it to another group. Before, the group accepted the
job and it sat waiting for hours while other Macs had nothing to do.

**Pausing hands waiting work back.** When you pause the last Mac in a group that could make a waiting job, the job goes
back to the Gateway within a few seconds and is made somewhere else. Nothing is charged for it. Jobs that are already
being made finish as normal. A Mac that is switched off but not paused still counts: it may come back.

### Voices run on 8 GB Macs again

**OmniVoice and VoxCPM2 stopped failing on 8 GB Macs half-way through a voice-over.** The memory check ran again before
every section of a voice-over, while the voice was already loaded for that same job, so it found too little free memory
and stopped the job. It now checks once, before the job starts.

**A Mac makes room for the voice you asked for.** If a Mac still has a different voice loaded from its last job, it
unloads that one first instead of saying it is short of memory.

**"Not enough memory" messages now tell the truth.** When a Mac does hand a job back for memory, the message shows the
free memory it actually saw when it decided, not a reading from another moment (some said "needs 3.1 GB and has 4.5 GB").

**A Mac's model list no longer says "not enough memory" just because it is busy for a moment.** It now says whether the
Mac can run the model at all; free memory is checked when a job is about to start.

### Shop Macs can help the fleet without getting in anyone's way

**A shop's cashier Mac can now lend its spare time to the fleet.** Turn on Workstation Mode for it and Studio Hub
works quietly in the background: its jobs get the lowest priority, so the till, Chrome and Telegram always come first,
and it shows no pop-ups or alerts.

**It only makes the six lighter models.** FLUX.2 Klein, Z-Image-Turbo, Qwen3-TTS 0.6B, VoxCPM2, Whisper turbo and
Stable Audio 3. A job for any other model goes to another Mac.

**It can wait for the Mac to be free.** You can set it to work only when nobody has touched the Mac for a few minutes,
to step aside when someone starts using it, and to take no work at all during hours you choose. A job it has started always finishes;
only if the Mac runs critically short of memory does it stop the job, and then another Mac takes it straight away.

**Set it up from the drive.** The kit's Extras folder has "Set Up a Shop Mac" for each cashier Mac (it copies only the
six models, about 15 GB) and "Set Up the Shops Controller" for the shop's always-on Mac mini. The settings for each Mac
are then changed from the Gateway's Fleet page.

### Technical notes

Release of `release/0.6.141` (= 0.6.140, which was never published, + the fixes below). Found by the TTS comparison test of 2026-10-04
(`_orchestra/audit-20261001/tts-comparison/`). Gateway contract: `_orchestra/audit-20261001/INTERFACES-0141.md`.
- **Never strand work in a paused group** (`broker.every_capable_mac_paused`, `_hand_back_queued`, `standalone.submit_jobs`). Pauses were read only
  when choosing a Mac (`_eligible_studios`, `_pool_members`), never at accept, so group 1500 with all five Macs paused accepted 21-25 TTS jobs that sat
  queued ~7,000 s. Now: when at least one Mac that could run the model is known and every such Mac is paused (machine or Studio) or drained,
  `POST /api/hub/jobs` answers **503** `{"error", "code": "NO_AVAILABLE_MACHINE", "detail"}` (replays of an accepted `clientRequestId` excepted), and the
  dispatcher hands every queued item of such a batch back as `error` / `error_code: NO_AVAILABLE_MACHINE`, no try spent, webhooks after the durable write;
  running items finish. Offline/busy/cooling Macs still count as capable. `_pool_members` now shares `_model_rows` / `_paused` / `_can_run` with it.
  Broker lanes only (voice, image, music, video, render); chat and transcription keep their own queues.
- **Voice memory guard admits a job once** (`generation._memory_preflight`, `_dispatch_mlx_long_form` `_memory_admitted`). The guard ran before every
  section with the job's own model (and the section validator) resident, so OmniVoice / VoxCPM2 parts on idle 8 GB Macs failed "2.8 GB free is needed,
  but only 2.0-2.5 GB is available" (the loaded floor 2.75 GB) while the host reading showed 3.0-4.6 GB; 0 successes on 8 GB since 00:25. Later
  sections and quality re-renders of an admitted job are no longer re-measured.
- **A different idle model is freed before refusing** (`_memory_preflight`): a worker holding model A, asked for B, evicts A (the load does anyway)
  and re-reads, instead of refusing B on memory A held. The Hub's admission hand-off (`release_idle_siblings`) now also asks the idle target Studio
  itself to release, for the same reason.
- **The hand-back sentence quotes the decision's numbers** (`broker._insufficient_memory_text`): it parses the refusal's own sentence (Hub gate
  "needs ~X GB, ~Y GB free" or Voice MemoryGuard "X GB free is needed, but only Y GB is available") instead of re-reading policy and a cached host
  snapshot afterwards (116 of 272 refusals said e.g. "needs about 3.1 GB free and has about 4.5 GB free"). The admission-wait expiry now uses the floor
  the gate used (`_with_machine_floor`, a workstation's start floor included).
- **Voice `memory_eligible` is capacity only** (`model_runtime_status`): total memory >= the model's tier, like every other Studio and as
  `capabilities` / `model_health` / the Gateway read it. It used to also need free memory at that instant, so a busy or briefly-full 8 GB Mac published
  OmniVoice as not memory-eligible.
- Tests: `tests/hub_regression/cases/test_paused_group_hands_back.py` (new), `test_capacity_wait_backoff.py` (worker numbers win over a 4.5 GB snapshot),
  `tests/test_memory_policy_contract.py` (target released too), voice `test_priority_mlx_models.py`, `test_native_executor.py`, `test_tts_quality.py`,
  `test_generation_artifact_contract.py`.

## 0.6.140 — 2026-10-03 · Workstation mode: a shop's cashier Mac lends the fleet its idle time and never its user's

### Technical notes

Never published on its own; it ships in 0.6.141.

Release of `release/0.6.140` (= 0.6.139 + `feat/workstation-mode` 5e5ad77 (incl. 9e44011), merged `--no-ff`, no textual conflicts; the branch was cut
from 3f59771, so it is checked against 0.6.139's uniform narration and kit Extras: no shared code path, broker edits in separate hunks).
- **Workstation mode** (`components/hub/workstation.py`, owner 2026-10-03 "Shops"): per-Mac settings in `<hub data>/workstation.json` (default off), set
  from the Gateway via the controller relay `POST /api/hub/fleet/nodes/{id}/workstation` -> the node's `/api/hub/workstation` (fleet token); published as
  `workstation` {settings, state off|working|standing_by|yielded|quiet_hours, reason, jobs_today, yields_today} on `/api/hub/resources` and every fleet row.
  When on: worker processes (and children) at `PRIO_DARWIN_BG`; only-when-idle (HID idle >= `idle_minutes`, default 3, or screen locked); step aside
  (input -> the current job finishes, model unloaded, no new work for `resume_after_minutes`, default 2); quiet hours; no desktop alerts.
- **Six-model allowlist** (`components/workstation_models.py`): FLUX.2 Klein 4B 4-bit, Z-Image-Turbo 4-bit, Qwen3-TTS 0.6B Base 4-bit, VoxCPM2 4-bit,
  Whisper large-v3 turbo, Stable Audio 3 Medium, each by package id and repo id; any other model is refused at the peer door
  (`WORKSTATION_MODEL_NOT_ALLOWED`). Checked against 0.6.140's `model-manifests/`: all six ship, none retired or shelved
  (`tests/test_workstation_mode.py` pins it).
- **Memory**: no fixed reserve; a job starts on the model's own free floor or the Mac's `start_free_gb` (`broker._with_machine_floor`). A started job
  finishes; only macOS critical memory pressure for 20 s cancels it and hands it back as **`WORKSTATION_YIELDED`** (receipt kept, worker stopped if it
  does not stop in 2 s).
- **Controller** (`broker.py`, `peers.py`, `capabilities.py`, `fleet_nodes.py`): a standing-by Mac is busy, never failed; a `WORKSTATION_YIELDED` item
  is re-queued on another free Mac of the group or handed straight back to the Gateway, no try spent; stand-by (30 s) / not-allowed (600 s) refusals are waits.
- **Native**: the app reads `workstation.json` - no window / What's New / failure pop-ups, optional hidden menu-bar icon.
- **Kit**: `packaging/kit-extras/Set Up a Shop Mac.command` (workstation mode on, `STUDIOHUB_WORKSTATION=1` copies only the six models, then
  Update this Mac) and `Set Up the Shops Controller.command` (own generation paused via `machine_flags.json`); `prepare-kit-extras.sh` copies both into
  Extras beside the Yam Display installer. `qualification/NODE-SETUP-CHECKLIST.md` "Shops". Tests: `tests/test_workstation_mode.py`, `tests/test_shop_kit.py`,
  `tests/hub_regression/cases/test_workstation_yield.py`, `tests/test_yam_display_install.py`.

## 0.6.139 — 2026-10-03 · One pause rule for every voice, 250-character sections and 1,500-character requests; the kit installs Yam Display

### One pause rule for every voice

**Every voice now pauses the same way.** Whatever voice you pick, there is a 0.6 second pause after a sentence and a 1 second pause at
a new paragraph where the pieces of a voice-over are joined. A very long sentence that has to be cut in the middle gets only a short,
soft join, so you do not hear a gap mid-sentence. You can still choose your own pauses for a job; then those are used instead.

**Every voice is made in pieces of up to 250 characters.** Shorter pieces come out cleaner and are quicker to make again if one fails.
The check that catches a long silence inside a piece allows for the new pauses.

**Every voice takes up to 1,500 characters per request.** Studio Hub tells Story Studio this for every voice, so a chapter is sent in
requests of that size and each one finishes in good time.

### Yam Display comes with the kit

**The setup drive now installs Yam Display.** "Install the app" and "Update this Mac" install it too, if it is missing or older. It is never
downgraded, and if it cannot be installed, Studio Hub still installs normally.

### Technical notes

Release of `release/0.6.139` (= 0.6.138 + `feat/kit-yamdisplay` 3f59771 on main + `feat/uniform-narration` 52eb9a7 (incl. 37b0971 paragraph pause), merged
`--no-ff`, no conflicts).
- **One join rule for every voice family** (37b0971, 52eb9a7, `long_form_policy`): `UNIFORM_SENTENCE/PARAGRAPH/SOFT_JOIN_PAUSE_SECONDS` = 0.60 / 1.00 / 0.15 s.
  `production_boundary_pause_seconds` returns the uniform rule; `PRODUCTION_PAUSE_FAMILIES` / `uses_production_pauses` are gone (Breeze's flat 0.6 s included).
  A request's `sentence_pause_sec` / `paragraph_pause_sec` (0-3 s) replace the rule (`requested_boundary_pauses`). The lane contract publishes defaults
  paragraph 1.0 / sentence 0.6 for every model.
- **250-character sections for every TTS model** (`UNIFORM_SECTION_MAX_CHARACTERS`, `policy_for(..., uniform=True)`), join 600 ms published; a finer audit is
  still followed (min), a caller may still choose 40-3000. Audit records are not edited: `audit_contract_runtime` checks a record against the family's
  measured policy (`uniform=False`) and `qwen_17b_assembly_invariants` keeps the audited 300/600/180 ms.
- **`lane_contract.maximum_input_characters` = 1,500 for every TTS model** (`UNIFORM_REQUEST_CHARACTERS`, `evidence: owner_uniform`); a model whose slowest
  Mac was timed and fits less in 900 s publishes the smaller number (`evidence: measured`). Breeze stays 1,500.
- **Quality gate**: the in-section silence allowance follows the uniform (or requested) pause + 3.5 s: sentence 4.1 s, paragraph 4.5 s (was 3.8 / 4.1).
- Catalog wording, voice README, `docs/lane-execution-contract.md`, `docs/model-benchmarks.md` (via `qualification/model_record.py`) updated.
  Write-up: `_orchestra/audit-20261001/uniform-narration.md`. Tests: `tests/test_tts_join_pauses.py` (new), `test_section_policy.py`, `test_breeze_family.py`,
  voice `test_lane_contract.py`, `test_long_form_policy.py` and others.
- **The kit installs Yam Display** (3f59771): `packaging/kit-extras/Install Yam Display.command` (installs when missing or older, never downgrades, never blocks the
  Studio Hub install), run by `1 - Install the app.command` and `Update this Mac.command`; `prepare-kit-extras.sh` copies the DMG and command into a kit's
  Extras with sha256 checks. Tests: `tests/test_yam_display_install.py`.

## 0.6.138 — 2026-10-03 · Every voice section is also checked for a long silence inside it or a dropped clause, and re-made if it has one

### Voices with a long silence in the middle, or a missing sentence, are made again

**A piece of a voice-over with a long silence inside it is now caught.** In 0.6.137 a piece could pass its check with half a minute of
silence in the middle, because the rest of it sounded fine. Studio Hub now measures the longest quiet stretch between the first and last
words. If it is far longer than a natural pause (about 4 seconds at normal speed), the piece is made again, the same way as other
failed pieces. This check needs no extra memory, so 8 GB Macs do it too.

**A dropped phrase is now caught.** When the Mac listens back to a piece, it used to allow a few missing words anywhere. If more than three
words in a row are missing, a whole phrase was dropped, and the piece is now made again.

### Technical notes

Release of `release/0.6.138` (= 0.6.137 + `fix/gate-internal-silence` f5d7946, merged `--no-ff`, no conflicts).
- **Section gate: unnatural silence inside a section** (f5d7946, `tts_quality.py` evidence revision v2, `broker.py`). EP0003 Chapter 9 part 5 passed the
  0.6.137 gate with 35 s of silence (-66 dB) inside a 67 s section, and Whisper heard 69 of 76 words (7 allowed) - the 7 were one clause lost in the silence.
  `audio_metrics` now records `longest_pause_s` (longest unvoiced run between the first and last voiced 20 ms frame, after DC/drift removal, so edge silence
  does not count). `TTS_SECTION_INTERNAL_SILENCE` when it exceeds the longest pause the text calls for (`long_form_policy` paragraph 0.6 s if the text has a
  blank line, else sentence 0.3 s) + 3.5 s, divided by native speed (3.8 s / 4.1 s at speed 1); re-rendered with a new seed like the other codes. No model
  is needed, so 8 GB Macs get this check too. The 3.5 s margin: Breeze 4-bit's own narration pauses reach 3.2-3.4 s; good clips stop at 3.44 s, the next
  is 4.32 s.
- **Transcribe-back: a dropped clause fails** even when the 10% share holds: `TTS_SECTION_TEXT_MISMATCH` also when more than 3 consecutive expected words
  are not heard (`longest_missing_run`). Broker whitelist carries the new code and `longest_pause_s`, `max_pause_s`, `longest_missing_run`.
- Offline on saved audio: 19/19 bad eval sections caught (18 by audio alone, was 7); 8 eval sections labelled good are now rejected, each with a 4.3-18.9 s
  mid-section silence; 4 of 18 accepted Chapter 9 sections (35, 12.3, 7.6, 6.7 s) rejected, 14 kept (`_orchestra/audit-20261001/tts-gate-internal-silence.md`).
  Tests: `test_tts_quality.py` (+3), `tests/test_section_quality_passthrough.py` (+1).

## 0.6.137 — 2026-10-03 · Every voice section is checked before it is joined and re-made if it came out wrong; Breeze takes chapters in pieces it can finish in time

### Voices are checked piece by piece, and long chapters are split into pieces that finish in time

**Every piece of a voice-over is checked before it goes into the finished file.** A long text is voiced in pieces. Each piece is now
checked: is it silent, or just a hum, or much too long for its words, and (when the Mac has the memory for it) does it actually say
the words it was given? A piece that fails is made again, up to three more times. If it still fails, the job stops and tells you which
piece was the problem, instead of handing back audio with silence, humming or missing sentences in it.

**Breeze no longer runs on for a minute and a half on a short sentence.** Each Breeze piece now gets only as much time as its words
need, which is where the long stretches of humming and babble came from.

**Breeze chapters are split into pieces a slower Mac can finish.** Studio Hub now tells the Gateway the most text one Breeze request
should carry (1,500 characters), worked out from the slowest Mac allowed to run it, so a long chapter is sent in parts instead of
running out of time.

### Technical notes

Release of `release/0.6.137` (= 0.6.136 + `fix/whisper-gaps-tts-deadline` c6ea53b + `fix/tts-quality-gate` f115fb5, both merged `--no-ff`, no conflicts;
f115fb5 is built on c6ea53b).
- **TTS size per request from the slowest Mac** (c6ea53b, `catalog.py`, `lane_contract.py`). Each TTS model whose slowest allowed Mac class was timed
  (`catalog.SlowestMacSpeed`) publishes `lane_contract.maximum_input_characters` = floor(900 s / RTF x chars-per-audio-second, to 100) with
  `maximum_input_characters_source`. Breeze TTS 2 4-bit: M1 16 GB, RTF 5.13, 8.58 chars/s -> **1,500** characters (one request <= 15 min on an M1 16 GB,
  2x under the 1,800 s voice deadline). The Gateway scales its deadline by this field and Story splits narration by it. Other TTS models publish nothing.
  The Whisper subtitle gaps were investigated first: Whisper drops nothing; the audio itself was silent, DC drift or babble from failed Breeze sections
  (`_orchestra/audit-20261001/whisper-gaps-tts-deadline.md`). Test: `test_lane_contract.py`.
- **Per-section TTS quality gate** (f115fb5, new `tts_quality.py`, `generation.py`, `broker.py`). Every mlx-audio family (Breeze, VoxCPM2, OmniVoice, Fish,
  Chatterbox, Qwen, Pocket) now runs through the parent section loop, single-section requests included (that one section is moved into place, not joined or
  levelled). Each section gets audio checks (DC/drift removed with a 50 ms moving average, voiced = 20 ms frame > -40 dBFS; `TTS_SECTION_SILENT` below
  chars / (fastest rate x 2.5), `TTS_SECTION_TOO_LONG` above chars / (slowest rate x 0.55) + 2.5 s; Breeze 8.6-11.7 chars/s, others 6-20; CJK counts 3x)
  and a Whisper large-v3 turbo transcribe-back (`TTS_SECTION_TEXT_MISMATCH` when > max(2 words, 10%) are not heard) loaded only when turbo + 2 GB is free;
  a skipped check says why (`unavailable`, `memory`, `script`, `qwen_clone_gate`). A rejected section is re-rendered with a new seed (+7919 per attempt)
  up to 3 times, then the job fails with `TTS_SECTION_*` and "Section N of M ...". Breeze `max_tokens` is now bounded by the section's text (it ran to
  `generate_audio`'s 1,200 = 96 s). Evidence: job field `section_quality {revision, sections: [...]}`, persisted, and copied onto the batch item by
  `broker._record_worker_section_quality` (numbers, booleans and known codes only; cleared on a re-attempt). Measured offline on 131 Dreamlands sections:
  19/19 bad rejected (7 by audio checks alone), 0/112 good rejected; 10/10 new-seed re-renders accepted; Whisper ~4% of Breeze generation time, +1.6 GB
  while a job runs (`_orchestra/audit-20261001/tts-quality-gate.md`). Tests: `test_tts_quality.py`, `tests/test_section_quality_passthrough.py`.

## 0.6.136 — 2026-10-03 · A busy group no longer blocks its controller's update; the app relaunches only after a real crash; free work is re-run when its Mac or worker dies

### Updates no longer wait for ever, Studio Hub only reopens after a crash, and lost work is redone

**A busy group of Macs no longer holds back its update.** When an update is ready, the main Mac of a group finishes its own work and then
pauses the whole group: the other Macs finish what they are doing, take nothing new for a moment, and the update installs. Before, a long
queue could keep the group busy so the update never got its turn.

**Studio Hub reopens by itself only after a crash.** If Studio Hub crashes, it opens again on its own. If you choose Quit, use Force Quit,
or macOS closes it because the Mac ran out of memory, it now stays closed.

**See why a Mac went offline.** Each Mac now remembers why Studio Hub last stopped (you quit it, it crashed, Force Quit, low memory, an
update, or the Mac was shut down), and the Gateway can show that next to a Mac that is offline.

**Work is redone when a Mac or studio stops in the middle of it.** If a Mac goes quiet, or one of its studios stops while making something,
that job is now started again on another Mac (or on the same one once it is back) for every kind of job: images, music, video and voices.
If the first try turns out to have finished after all, its result is kept and the second try is stopped.

### Technical notes

Release of `release/0.6.136` (= 0.6.135 + `fix/drain-never-starves` e505ad9 + `fix/crash-only-and-reroute` b65a9ad, both merged `--no-ff`, no conflicts;
both touch `broker.py`/`standalone.py` in separate hunks - drain gating in `busy_machines`/`_submission_readiness`, re-runs in `_run_item`'s failure path -
and a re-run is dispatched through the same `busy_machines` gate, so a held drain defers re-runs like any new work).
- **Drain never starves** (e505ad9, `broker.py`, `standalone.py`). A draining controller's nodes keep working while its own job(s) finish; once nothing runs
  on the controller itself (`broker.drain_holds_group`), `busy_machines` names every machine in the group and new submissions get the re-routable
  `MACHINE_BUSY`. What the nodes run finishes, the count reaches zero, the controller installs. Before, a long queue kept some node running one of the
  controller's batches for ever, so the controller - holding the group's one update slot - and every Mac behind it never updated. Test: `test_update_drain.py`.
- **Relaunch only after a real crash** (b65a9ad, `native/ShellCore.swift`, `native/StudioHub.swift`). `StopRecord` settles why the last run ended from a
  `running` heartbeat, crash-signal (`crashed`), SIGTERM (`terminated`), `memory-critical`, `clean-quit`, `updating` and `power-off` markers, backed by
  `StudioHub-*.ips` reports. The agent's standby exits 1 (launchd relaunches) only for a crash; a Quit, Force Quit or memory kill exits 0
  (`AgentStandby.shouldStayStopped`), so the existing plist (KeepAlive SuccessfulExit=false) needs no migration.
- **Why a Mac went offline** (b65a9ad, `fleet_nodes.py`, `standalone.py`): the verdict is written to `<data>/last-stop.json` and published as
  `last_stop {reason, at, label, relaunched}` on `/api/hub/resources`, the fleet overview rows and `/api/hub/fleet/nodes` rows; a controller relays each
  node's, and repeats an unreachable node's last word marked stale. Test: `test_last_stop.py`.
- **Free work is re-run when its Mac or worker dies** (b65a9ad, `broker.py`, `transcription_jobs.py`). An attempt whose worker stopped answering for the
  recovery window, lost the job (404) or restarted under it (`WORKER_RESTART_UNCERTAIN`; Voice `EXECUTION_IDENTITY_LOST`) is re-run in every lane (image,
  music, video, voice; this Mac or a node) instead of being left `uncertain`. The abandoned original is watched for `ABANDONED_WATCH_S`: cancelled when its Mac
  answers, or adopted if it finished first (the re-run is then cancelled). A voice re-run takes a fresh client request id. Transcription lost to a dropped or
  timed-out worker is re-run elsewhere at once. Malformed receipts and a lost local POST receipt stay fenced.
  Tests: `hub_regression/cases/test_free_work_reroute.py`, native StopRecord/AgentStandby checks in `tests/native/api_main.swift`.

## 0.6.135 — 2026-10-03 · One worker exiting no longer stops the Hub; a crash or Force Quit brings the app back; pause a Mac from the Gateway

### Studio Hub keeps itself running, and you can pause a Mac

**One studio stopping no longer stops everything.** If one studio (for example Chat) stops unexpectedly, Studio Hub now starts that studio
again by itself after a short wait. The other studios, and the jobs they are running, carry on as normal.

**Studio Hub comes back after a crash or a Force Quit.** If Studio Hub crashes, or is closed with Force Quit (for example when the Mac runs
low on memory), it now opens again by itself. Choosing Quit from its menu still closes it and it stays closed.

**Saved voices from this Mac stay on this Mac.** A voice that only exists in this Mac's own library is now only used here, instead of
being sent to another Mac that does not have it.

**Pause a Mac from the Gateway.** A Mac can now be paused: it finishes the work it is doing, takes no new jobs, and stays in the fleet.
Resume it and it starts taking work again.

### Technical notes

Release of `release/0.6.135` (= 0.6.134 + `fix/worker-exit-resilience` 214ad11 + `feat/pause-node` 1194f1b, both merged `--no-ff`, no conflicts).
- **A worker that exits is restarted on its own** (214ad11, `packaging/hub_worker.py`). A private worker that exits after start-up is restarted on its own
  port with backoff (1, 2, 5, 10, 30, 60 s; reset after 5 min up); only its lane is down meanwhile and the Hub, the other studios and their in-flight jobs keep
  running. A worker failing before the Hub is ready is still fatal. `run_server` gains a `shutdown_started` hook so no child is restarted once shutdown begins.
- **The app is supervised by its launch agent** (214ad11, `native/ShellCore.swift`, `native/StudioHub.swift`). An app opened by hand or reopened by an update
  was supervised by nothing (the agent is only loaded at login); such a launch now loads/kickstarts the agent. The agent's copy waits on the data-folder lock
  before any AppKit setup and, when the app ends, exits 0 after a deliberate Quit (clean-quit marker) or 1 otherwise, so launchd relaunches after a crash or
  Force Quit and a clean Quit stays stopped. Default data folder only.
- **An unmanaged voice stays on this Mac** (214ad11, `broker.py`): a `voice_library_id` the Hub does not manage is this Mac's own library entry and is now
  routed only to the studio that holds it (was: any studio, so it could go to a node without that voice).
- **Pause or resume a Mac** (1194f1b): Fleet-token `POST /api/hub/fleet/nodes/{id}/pause {"paused": bool}` flips `registry.machine_enabled` (already read by
  every modality's dispatch) for a joined node or the controller itself, persisted in `machine_flags.json`; running jobs finish, the Mac stays joined, and the
  roster row publishes `paused`. Fleet-token only (not in `NODE_ALLOWED`). Tests: `test_hub_process.py`, `tests/native/api_main.swift`,
  `hub_regression/cases/test_unmanaged_voice_routing.py`, `test_node_pause.py`.

## 0.6.134 — 2026-10-03 · Image, music and video jobs finish on other Macs; a busy Mac is a wait; abandoned peer work is cancelled

### Thumbnails, music and video finish on your other Macs

**Jobs sent to another Mac now finish there.** A controller wrongly rejected the first progress update from every image, music or
video job running on another Mac, so a job that took more than two minutes - such as a Z-Image thumbnail on an 8 GB Mac - was dropped and
started again on the next Mac, over and over, and never finished. These jobs now run to the end on the Mac that took them.

**A Mac no longer stays "reserved for other work" after its job is done.** A Mac now frees itself as soon as its job ends, instead of
refusing new work for up to six hours.

**A busy Mac means "wait", not "failed".** Subtitles and Chat now wait for a free Mac instead of giving up with "reserved for other work on
this Mac" when the Macs they tried were busy.

**Work that is given up on is stopped.** When a controller gives up on a job on another Mac and starts it elsewhere, it now tells that Mac to
stop, so the Mac is not kept busy on work nobody will use.

### Technical notes

Release of `release/0.6.134` (= `fix/image-machine-id-reserved`: 0.6.133 + 2e29bea + ec170ac + 59b6ec6).
- **Peer jobs are polled to their own answer** (2e29bea). The controller's dispatch poll and the manual reconcile in `broker.py` called
  `_validate_durable_worker_job` without `remote=`, so a joined node's worker (which reports `machine_id: "local"`) failed the first poll of every peer
  image/music/video job ("Image worker status changed its machine_id"). Only the recovery poll passed `remote`, so a peer job survived only if it finished
  inside `RECOVERY_WINDOW_S` (120 s): Z-Image-Turbo 4-bit (~300 s on 8 GB) never finished remotely and was regenerated on node after node.
- **A node's peer lease ends with its job** (2e29bea): `standalone._release_finished_peer_leases` asks the node's own worker and frees a lease once the job
  is over or gone, instead of waiting for a controller poll that never came (six-hour TTL, "local is reserved for other work on this Mac" while idle).
- **MACHINE_BUSY is a wait in Subtitle (2e29bea) and Chat (ec170ac)**, as in the broker since 0.6.111: try restored, Mac not marked failed, retried after
  `MACHINE_RESERVED_AVOID_S`. Before, three busy nodes ended a transcription, and `MAX_TRIES` busy answers ended a chat pack, as a final error. Any other
  409 is unchanged.
- **The controller cancels peer work it gives up on** (59b6ec6): when a remote Image/Voice job is abandoned for regeneration elsewhere,
  `_cancel_abandoned_peer_job` sends the node the user-cancel route (`DELETE /api/generate/jobs/<id>` via the peer surface) in the background and logs it.
  Tests: `test_remote_worker_identity.py`, `test_subtitle_dispatch.py`, `test_studio_proxy.py`, `test_chat_hub_wiring.ChatPackMachineBusyTests`.

## 0.6.133 — 2026-10-03 · Fleet health follows what each Mac really runs; a renamed Mac shows its new name; rename a Mac from the Gateway

### Fleet health shows real problems only, and renamed Macs show their new name

**Idle Macs no longer show as needing attention.** Macs that joined while the Video studio still shipped kept a Video worker on their
controller's list, so Fleet showed "workers offline" for a worker that no longer exists and kept every idle Mac amber. Each Mac's
studios now follow what that Mac actually runs.

**An old failed job stops holding a Mac in "Needs Attention".** After 15 minutes a failed job no longer keeps the Mac amber; it goes back
to Ready like any other finished job. A studio that should be running and is not still shows as needing attention.

**A renamed Mac shows its new name everywhere.** When you rename a Mac, its controller and the Gateway now pick up the new name from the
Mac itself, within a minute or so.

**Rename a Mac from the Gateway.** Studio Hub is now ready for the Gateway's new Rename option for a Mac in the fleet. The name is changed on that Mac first, so if the Mac is
switched off it keeps its name and you are told why.

**Connections shows "Machine ID".** On a Mac that is not a controller, Connections now shows its Machine ID, which stays the same when the
Mac is renamed. A Mac handed over to a new controller now joins under its name instead of its ID.

### Technical notes

Release of `release/0.6.133` (main at 9a7e786 = 0.6.132 + 8cff7b1 fleet health + 9a7e786 node rename; no merge needed).
- **Fleet health: a node's studios follow the node** (8cff7b1). Studios were recorded on the controller's roster once, at join, and nodes do not
  re-join, so every node that joined while Video shipped kept a `video` studio: the controller polled a worker that does not exist, showed "workers
  offline" and held every idle Mac in `needs_attention` (activity `partial_read`). `fleet_nodes.sync_roster_from_snapshots` now makes each node's roster
  studios follow what its authenticated peer snapshot says it runs, both ways; silence, errors and empty answers never change a row, and the monitor
  reloads its registry when a row changed.
- **An old error stops holding a Mac amber** (8cff7b1): `activity._state_at` treats an error as `needs_attention` only for `JUST_FINISHED_S` (15 min);
  after that the Mac ages to ready / long idle like any finished job. A studio that should run and is down still needs attention.
- **A renamed Mac's name reaches its controller** (9a7e786). `/api/hub/resources` carries `machine: {id, name}`, the peer snapshot keeps it, and the
  roster sync adopts the name only from an authenticated snapshot naming that node id with a valid 1-120 character name (older Hubs and other Macs change
  nothing). Before, the name moved only at join and through the node's own-credential status call, so a node whose credential the controller no longer
  recognised kept its old name forever.
- **Rename a Mac from the Gateway** (9a7e786): new Fleet-token route `POST /api/hub/fleet/nodes/{id}/name` (`local`/own id renames the controller).
  `relay_name` renames the node on the node first (`POST /api/hub/machine-label` with the Fleet token) and only then the roster, and drops the
  pre-rename snapshot. Rule: a Mac's name lives on the Mac, so a Mac that is off or refuses keeps its name and the caller is told why. Neither route is in
  `NODE_ALLOWED`; a fleet-credential `machine-label` needs 1-120 printable characters (no clearing).
- **Handover join uses the machine name** (9a7e786): `_register_with_controller` read `machine_name` from `load_settings` (never there), so a handed-over
  Mac joined under its id; it now reads `public_settings`.
- Connections > Local API on a non-controller says "Machine ID" (stays the same after a rename) instead of "Controller ID"; the Fleet-token line says it
  may rename this Mac. Tests: `tests/test_fleet_health_idle.py`, `tests/test_node_rename.py`, `test_machine_name_and_restart` pins the fleet rename rule.

## 0.6.132 — 2026-10-02 · New Studio Hub icon (Wave Frame), fleet-password rotation no longer strands offline Macs, unregistered voice outputs cleaned

### A new Studio Hub icon, and changing the fleet password is safe for offline Macs

**A new Studio Hub icon.** The app has a fresh look: a picture frame holding a wave and a note, on a dark blue tile. You will see it in the Dock,
in Finder, in the About window and in the menu bar, where it follows light and dark mode.

**Changing the fleet password no longer leaves Macs behind.** A Mac that was switched off or away when you changed the password now keeps working
with its old one for up to 30 days. As soon as it comes back online it is given the new one, and the old one is retired. Connections shows how many
Macs are still on the previous password. We also fixed a bug where a controller kept the old password until it was restarted.

**Voice recordings that were never saved are tidied up.** Audio the app generated but never recorded is now moved to the Trash Folder after 7 days.
The first time this runs it moves any backlog there too. Nothing is deleted straight away: the Trash Folder empties itself after 15 days.

**A 16 GB test for Fish voices.** The kit now has a one-click test that measures Fish S2 Pro (4-bit, 8-bit and full precision) on a 16 GB Mac.

**More reliable tests.** A few tests that sometimes failed by chance now give the same answer every time.

### Technical notes

Release of `integrate/0.6.132` (merges fix/small-batch-0132, commit d9e3534, and feat/fish-16gb-test, commit 4fc4694, on 0.6.131; no conflicts, plus the logo commit).
- **New app icon: "Wave Frame"** (owner's pick, concept 2 of 6, 2026-10-02): a picture frame holding a wave with a note, on a dark indigo tile.
  Source of truth is `assets/logo/app-icon.svg` (1024 tile) and `assets/logo/menubar-icon.svg` (18 pt one-colour glyph);
  `packaging/make_app_icon.py` now renders both with AppKit's SVG renderer (each `.icns` size drawn at its exact pixel size, 16-1024 @1x/@2x,
  then `iconutil`) and writes `assets/app-icon.icns`, `assets/app-icon-1024.png`, `assets/menubar-icon.png` (18 px) and `assets/menubar-icon@2x.png`
  (36 px). The Dock, Finder, the About panel and the DMG show the `.icns` (`CFBundleIconFile`, unchanged). The menu-bar item was the SF Symbol
  `waveform.circle`; it is now the Wave Frame glyph as a template image (tinted for light/dark bars), loaded from the bundle by
  `AppDelegate.menuBarMark()`; `stage_voice.py` copies the two PNGs into `Resources` and refuses a build without them. The SF Symbol stays only as
  a fallback if the files are missing. `tests/test_app_icon_assets.py` pins sizes, the `.icns` types and the wiring.
- **Fleet token rotation grace (30 days):** changing the fleet password used to leave Macs that were offline during the change unable to rejoin.
  Nodes that missed a rotation keep working with the token they hold for 30 days, are given the new token when they next answer, and then the old
  one is retired; the app shows how many nodes are still on the previous token. Also fixed: controllers kept the old token in memory until restarted.
- **Never-recorded voice outputs are cleaned:** the disk governor now moves Voice audio the Hub never registered to the self-cleaning Trash Folder once
  it is older than the 7-day unacknowledged window. The first run moves any backlog there (nothing is deleted; the Trash Folder purges after 15 days).
- **16 GB Fish test:** the TTS runner gains Fish S2 Pro 4-bit / 8-bit / bf16 (opt-in via `--models`, `--target-gb`), `RUN-16GB-FISH.command` (refuses a
  non-16 GB Mac, imports exactly the three packages from the kit with `import_kit_packages.py`, then measures), kit README rows.
- Removed the unused `NativeWorkspaceStore.maskedToken`; four flaky tests made deterministic (`test_chat_hub_wiring`, `test_tts_8gb_runner` swap step,
  hub regression timeout, `test_shelved_models` import).

## 0.6.131 — 2026-10-02 · Passwords and tokens hidden behind an eye icon; memory floors from the real 8 GB measurement

### Passwords and tokens are hidden, and jobs start on 8 GB Macs

**Passwords and tokens are hidden behind an eye icon.** The Fleet token, the Hub token, the Hugging Face token and the token boxes for handing over
or joining a controller now show dots until you press the eye. Copy still works while they are hidden, and what you type is hidden too.

**Jobs start on 8 GB Macs showing about 3 GB free.** We measured real 8 GB Macs (an M1 and an M2). Voice and image jobs started cleanly from about
3 GB of free memory and never swapped, so the Hub no longer holds them back until 4 GB or more is free. It now starts a job once 2.8 GB is free,
which leaves a little room to spare. This applies to Qwen3-TTS 0.6B (8-bit and
4-bit), VoxCPM2 4-bit, OmniVoice, FLUX.2 Klein 4-bit and Z-Image-Turbo 4-bit.

**The 8 GB test reports are clearer.** They now say "Peak memory used" and "Started with N GB free" as two separate lines, so a peak is never mistaken
for the free memory a job needs before it starts.

### Technical notes

Release of `integrate/0.6.131` (merges feat/secret-protection, commit 4fe6d08, built on 0.6.129; no conflicts).
- **Secrets are masked (native app):** the Fleet token and Hub token on Connections, the new Fleet token after a rotation, the Hand-over and
  Join-a-controller token fields and the Hugging Face token are shown as 12 fixed dots (no prefix, suffix or length) until the eye is pressed;
  Copy works while masked; typed tokens use a secure field until revealed. `SecretValue` / `SecretInput` in `native/Design.swift`; the pixel check
  (two different secrets draw identical pixels while masked) runs inside the native lifecycle gate; `tests/test_secret_masking.py` pins that every
  token shown or typed uses them. No auth change.
- **Free-memory floors = 2.8 GB** (the FREE memory a job needs before it starts, NOT its peak) for Qwen3-TTS 0.6B 8-bit and 4-bit, VoxCPM2 4-bit,
  OmniVoice bf16, FLUX.2 Klein 4-bit and Z-Image-Turbo 4-bit. The owner chose 2.8 GB on 2026-10-02 for headroom (live idle 8 GB Macs show 3.27-5.22 GB
  free). Source: the real 8 GB M1 + M2 runs on 0.6.128 (1406 / 1407, 2026-10-02): jobs started
  cleanly from 3.09-4.65 GB free and peaked at 3.65-4.41 GB (voice) / 2.16-2.97 GB (image) with ~0 swap, so the old floors (4.1-4.6 GB for voice, taken
  from peak + margin) made healthy 8 GB Macs wait for memory they did not need. Each floor's `min_free_memory_note` says what was measured.
  Where it lives: the Voice catalog rows (`components/voice/app/backend/catalog.py`); the Image rows (`generation.py`); and for FLUX.2 Klein the
  Hub's `memory_admission._FLEET_DEFAULTS`, which OUTRANKS the worker's catalog row for the free floor (2.0 -> 2.8), so it is set in both. Z-Image-Turbo
  has no fleet default (it would also override its manifest's 8 GB total tier); its catalog row went 3.0 -> 2.8. Breeze and Fish floors unchanged.
- **8 GB runners no longer confuse peak with floor:** the TTS and image runners' summary printed "Recommended free-memory requirement: peak + 0.25 GB",
  which is how the floors above were set too high. It now prints "Peak memory used: X GB" and "Started with Y GB free" (lowest of the clips), and the CSV
  verdict says "peak memory used X GB" instead of "real need about".

## 0.6.130 — 2026-10-02 · Even Fish / VoxCPM2 / Chatterbox / Pocket pauses, Fish 4-bit on 16 GB, Breeze back to 16 GB only

### Proper pauses for Fish and more voices, and a lighter Fish on 16 GB Macs

**Pauses you can hear.** Fish, VoxCPM2, Chatterbox and Pocket now pause between sections the way OmniVoice and Qwen already did: a short
breath (0.3 s) after a sentence and a longer one (0.6 s) at a paragraph break, instead of one flat, barely audible gap.

**Fish Audio S2 Pro 4-bit now fits 16 GB Macs.** Its measured memory use is about 7.4 to 8.1 GB, so it is offered from 16 GB instead of 24 GB.

**Breeze is for 16 GB Macs again.** The earlier trial that copied Breeze to 8 GB Macs is undone; it needs about 5 GB, which is too tight there.

### Technical notes

Release of `integrate/0.6.130` (merges fix/pauses-tiers-0130, commit 645d542; no conflicts). Owner decisions 2026-10-02:
- **Pauses:** every family that joined sections with the flat 0.12 s pause (Fish, VoxCPM2, Chatterbox, Pocket) now uses the same text-led pauses
  as OmniVoice and Qwen3-TTS 1.7B: 0.30 s after a sentence, 0.60 s at a paragraph break.
- **Fish Audio S2 Pro 4-bit moves from the 24 GB tier to the 16 GB tier** (from the measured 7.4-8.1 GB S2 peak; catalog row, manifest
  qualification, capture spec, digest pin and benchmarks page agree). The 8.4 GB free floor is unchanged.
- **Breeze TTS 2 4-bit is carried to 16 GB Macs only again** (the 0.6.129 8 GB carry is reverted; its ~5 GB footprint is too tight on 8 GB).

## 0.6.129 — 2026-10-02 · Models can no longer be lost to a test, Fish S2 Pro 4-bit, Fish decodes in far less memory, Breeze on 8 GB Macs

### Models can no longer be lost by a test, and a lighter Fish voice

**Your models are safe from tests.** A test or check that used the real model library could move models into a temporary folder that was then
deleted, and the models went with it. Now a model library records which Hub owns it, and only that Hub may move anything out of it.

**New: Fish Audio S2 Pro 4-bit.** A lighter Fish voice (about 4.5 GB on disk) for Macs with 24 GB. It is made under the Fish Audio Research
License. It uses the codec from the 8-bit release, because the 4-bit download's own codec file cannot be read by the Hub's engine.

**Fish now uses far less memory.** Reading out a long script no longer balloons: the biggest memory use of Fish S2 Pro fell from about
14.6 GB to about 7.4-8.1 GB, with the same sound.

**Breeze on 8 GB Macs.** Breeze TTS 2 4-bit is now copied onto 8 GB Macs by the model copy step, so it can be tested there.

### Technical notes

Release of `integrate/0.6.129` (merges fix/library-ownership, feat/breeze-8gb-carry, feat/fish-4bit, in that order; no conflicts).
- **Library ownership (data-loss fix):** only the Hub that owns a model library may move packages out of it. A Hub started with a throwaway
  `--data-dir` against the real library (`~/StudioHubModels` or a kit's `model-library`) used to shelve/retire models into the throwaway
  directory's trash, so deleting the directory destroyed the weights. The library now carries an ownership record and a Hub that does not own
  it refuses to move anything; `qualification/verify-launch.sh`, `tests/test_peer_path_contract.py` and the other launch tests use a temporary
  library (`qualification/library_guard.py`, `tests/test_library_ownership.py`, `tests/test_no_real_library_launch.py`).
- **Fish Audio S2 Pro 4-bit (MLX)** (`majentik/fishaudio-s2-pro-MLX-4bit`, 24 GB tier, ~4.45 GB on disk, Fish Audio Research License recorded
  in the catalog): the package carries the codec from the 8-bit release (`codec.safetensors`, bit-identical tensors) because the 4-bit
  repository's own `codec.pth` is read by mlx-audio 0.5.7 as LLM weights and every job would succeed with noise. Free-memory floor 8.4 GB
  (measured, S2 peak 7.43-8.10 GB + 0.25 GB margin).
- **Fish decoding is memory-bounded** (`mlx_memory`, Fish family `bounded` profile): slabbed codec decode with context, reference encoded once,
  MLX cache capped. Fish S2 Pro peak 14.6 -> ~7.4-8.1 GB, same audio. Applies to every Fish tier.
- **Breeze TTS 2 4-bit is copied to 8 GB Macs** by the kit's copy step (owner-approved trial for the 8 GB TTS run).
- Merge note: the Fish branch's 8.4 GB free floor and `bounded` profile are kept as written; the Breeze copy rule and the ownership guard are
  independent of both.

## 0.6.128 — 2026-10-02 · Image and voice fit 8 GB Macs, Gemma retired, chat JSON fix, sidebar fix

### Image and voice now fit on 8 GB Macs, and a few things are tidied

**Images on 8 GB Macs.** FLUX.2 Klein and Z-Image-Turbo now use far less memory while they draw (Klein about 3 GB instead of about 9 GB),
so an 8 GB Mac no longer swaps for minutes or wears its disk. Larger Macs keep the model ready between jobs and are quicker for the next one.

**Voice-overs on 8 GB Macs.** VoxCPM2, Qwen3-TTS and OmniVoice now stay near 3 GB, and Breeze near 4.3 GB, with exactly the same sound. A long
script no longer runs the Mac out of memory.

**A lighter Qwen voice.** Qwen3-TTS 0.6B Base is now also offered in a smaller 4-bit version.

**Gemma is gone.** The Gemma 3 12B text model is retired and every Mac moves its copy to the Trash Folder (emptied after 15 days).

**Jobs do not wait for ever.** If a Mac cannot free the memory a job needs for about four minutes, the job is handed back so another Mac can
take it, with a plain sentence saying how much memory was needed.

**Chat fix.** Asking Chat for JSON together with a system message no longer fails.

**Tidier sidebar.** In a short window the sidebar scrolls and its footer no longer overlaps the list.

### Technical notes

Release of `integrate/0.6.128` (audit 2026-10-01 fixes; merges fix/retire-index, fix/kit-index-retired, fix/chat-json-system, fix/image-memory-8gb,
fix/tts-memory-ceiling, ui/design-pass-2). Highlights; the per-feature engineering notes follow.
- **Image memory (8 GB tier):** FLUX.2 Klein 4-bit footprint 9.4 -> 2.9 GB and Z-Image-Turbo 4-bit 7.5 -> 4.1 GB (staged + streamed, bounded VAE decode, MLX cache cap); the model stays loaded between jobs on larger Macs; all image jobs run on one inference thread, the JPEG encode runs off it.
- **Voice memory:** one codec decode per section no longer balloons (slabbed decode with context, reference encoded once, MLX cache capped): VoxCPM2 / Qwen3-TTS / OmniVoice peak ~3 GB, Breeze ~4.3 GB, with identical audio. New option: Qwen3-TTS 0.6B Base 4-bit (lighter, 8 GB tier).
- **Gemma 3 12B retired** (owner): every Mac, keep-all Macs included, moves its copy to the Trash Folder; 27 packages, ~78.0 GB.
- **Memory admission is bounded:** a job that waits ~4 minutes on the Hub's own free-memory floor, on a Mac with none of our work running, is handed back `INSUFFICIENT_MEMORY` (needs X, has Y) so the Gateway re-routes it. No free-memory floors were changed (Klein 2.0 fleet default, Z-Image 3.0); the measured footprints are in the catalog notes and the floors are to be set from the 8 GB runner.
- **Chat:** a JSON-mode request that also carries a system message no longer fails on Qwen3.5 / Gemma templates.
- **Kit:** retired models leave the kit index; Technical-folder runners updated (Qwen 4-bit in the TTS list, Klein's 8 GB tier pins).
- **UI:** the sidebar list takes the remaining height and scrolls, the footer is its own row and drops the counts line in a short window (no overlap at the minimum window).

### Gemma 3 12B 4-bit is retired everywhere

Owner, 2026-10-02: "it's bad ... remove it all over". `gemma-3-12b-it-4bit` / `mlx-community/gemma-3-12b-it-4bit` (LTX's text encoder; shelved
in 0.6.127) moves from `components/shelved_models.py` to `components/retired_models.py`. Every Mac - **keep-all-models Macs included** - moves its
copy into the self-cleaning Trash Folder at the next Hub start (`retired_models.retire_installed`, 15-day purge) and drops its `installed.json`
receipt; the kit never lists, stages or copies it; a job naming it is refused `MODEL_RETIRED`. Its manifest is parked in
`components/video/retired/` beside LTX's (dormant; the Video worker is not shipped), the capture rows are gone, and the package counts are
recomputed: **27 packages, 78,024,484,788 bytes (~78.0 GB)** in the catalog README, kit README, SSD-KIT-README, NODE-SETUP-CHECKLIST,
runtime-contract and `docs/model-benchmarks.md` (regenerated). Tests: `test_retired_models`, `test_video_retired`, `test_shelved_models`
(Gemma retired not shelved; a keep-all kit is not given it), `test_model_manifest_catalog`. Left for the coordinator: the 8.07 GB copy on each SSD's
`model-library/gemma-3-12b-it-4bit` (move to the trash, as for LTX).

### Image: memory-bounded generation for the 8 GB tier, resident models on larger Macs

**Cause (owner's real 8 GB M1 run, Hub 0.6.126, guard bypassed):** FLUX.2 Klein 4B 4-bit peaked at 9.8 GB of MLX memory and
swapped ~4 GB (3.9 GB written to the SSD) per image; Z-Image-Turbo 4-bit in `--low-ram` swapped 9.6-33 GB per image. Measured per stage:
FLUX.2's VAE decode alone peaks at 9-12 GB at 1280x720 (convolution/GroupNorm temporaries on a 0.17 GB model); mflux encodes the prompt
lazily, so the text encoder's weights stay alive into the first denoise step; `generate_image` keeps a closure (and, off M1/M2,
an `mx.compile` cache) over the transformer, so nothing is freed before the decode; and Z-Image's loop re-touches 3.5 GB of
transformer weights every step on a Mac with ~4 GB to give, which LRU evicts worst (dirty pages: swap, every step).

**Fix (`components/image/app/backend/`):** `staging.py` (evaluate embeddings then free the encoder; free the transformer when the loop
ends), `vae_bounded.py` (FLUX.2 decode in bands with global GroupNorm statistics; 12.1 -> 2.5 GB, PSNR ~50 dB vs mflux's decode, no
seams), `streaming.py` (transformer/encoder blocks loaded from their files one at a time and released: read-only, never swapped; the
pass over a model whose weights are used once costs nothing, the per-step re-read is ~0-8% of a step), MLX cache cap 0.5 GB. Tiers
(`generation.memory_plan`): below 12 GB staged + streamed; Klein 12-24 GB lean (cache cap + bounded decode); 24 GB+ unchanged. Image now
answers the shared five-mode memory policy (`auto` = `balanced` from 16 GB): a model whose plan keeps every component loaded stays
resident for the idle window (released on idle, the Hub's release, memory pressure, another configuration or a failed job; never
preempts a running job); all jobs run on one inference thread (MLX streams are per thread). `docs/mlx-memory-discipline.md` records the rules
for every future model; `tests/test_image_memory_profile_guard.py` requires every image model to have a profile or a documented exemption.

**Measured** (32 GiB M2 Max with an 8 GB Mac's MLX limits; the "before" reproduced the real M1 run's 9.8 GB; `qualification/evidence/image-memory-2026-10-01*`):
Klein 4-bit footprint 9.4 -> 2.9 GB (MLX 9.8 -> 1.6 GB), Z-Image-Turbo 4-bit 7.5 -> 4.1 GB (MLX 6.3 -> 2.8 GB), same seconds on this Mac;
Z-Image images byte-identical to before, Klein within bf16 rounding of the decode. Not yet confirmed on a real 8 GB Mac (swap, SSD writes):
re-run the 8 GB image runner (branch `runner/8gb-image`) with this build.

## 0.6.127 — 2026-10-02 · Disk protection, remote startup, compression at source, memory hand-back, real cancel, design refresh

### A Mac protects its own disk, starts by itself, and the app looks tidier

**Your Mac's disk is protected.** Each Mac now watches its own free space, clears old finished work in a safe order (into a Trash
Folder, never straight to deletion), and warns before the disk gets full. The Gateway shows a Disk column for every Mac.

**Macs start the Hub by themselves.** Each Mac reports whether it starts Studio Hub when you log in, and you can change that from
the Gateway without walking over to it. Macs set up from the drive start serving automatically.

**Smaller files straight from the Hub.** Music, voice-overs and images can come out already compressed (m4a audio, JPEG images)
when the caller asks for it, so less data travels and less disk is used.

**Out of memory? The job moves on.** If a Mac keeps saying it does not have enough memory for a job, the Hub now hands the job back
so another Mac can take it, instead of leaving it waiting.

**Cancel means cancelled.** When you cancel a running job and the Mac confirms it stopped, the job shows as cancelled.

**Video is gone, the app is smaller.** The LTX video model is retired and its engine is no longer part of the app (about 0.5 GB
smaller). The Gemma text model stays available on the install drives and on any Mac set to keep all models, but fleet Macs no longer
copy it.

**Easier to read.** The sidebar says which Mac this is and whether it is a controller, a joined node or standalone. Pages share one
layout, type and set of status colours; tables line up; the Music player and row buttons no longer overlap text.

### Video (LTX-2.3) is retired

**There is no video model any more.** The local video model, LTX-2.3, did not make good enough clips, so it is no longer offered, no
longer put on the install drives, and no longer part of the app. The app gets about 0.5 GB smaller, and the model kit goes from 28
to 27 model packages (about 19 GB less). The Video page now simply says there is no video model, instead of showing an empty list.
Story Studio never asks for it: it uses a cloud video service if one is set up, and otherwise the scenes stay still.
Clips you already made stay where they are on disk. The Gemma text model that used to ride along with LTX is not removed: it
stays available in case we use it for chat later. Music is unaffected, including sung songs.

### Technical notes

Release of `integrate/0.6.127` (audit 2026-10-01 follow-ups). Highlights; the per-feature engineering notes follow.
- **Disk governor:** a Mac keeps its own disk from filling (tiers, ordered reclaim, output caps, preflight, log rotation, 90-day Voice history, periodic job-history trim); `disk` object in `/api/hub/resources`.
- **Remote startup control + auto-start:** each Mac reports what it starts at login (`startup` object in `/api/hub/resources`) and the fleet can change it; joined Macs serve by default; kit install/update open the app with `--fleet-mode`.
- **Compress at source:** optional `output_format` for music, voice and image (m4a / jpeg), forwarded from the Gateway.
- **LTX retired + no Video worker:** app ~0.5 GB smaller; Gemma kept in the catalog but shelved (stays on the SSD / "keep all models" Macs only).
- **INSUFFICIENT_MEMORY hand-back:** after 3 memory refusals the job is returned to the Gateway so it can re-route; capacity back-off up to 300 s.
- **Cancel:** a cancel the worker confirms is `cancelled`, not uncertain (image/music/video/subtitle); delivery_fallback_reason carried onto the job item.
- **Speed/health:** cheaper resources and jobs listing, quieter service log, voice artifacts verified while streaming, device_settings atomic read-modify-write (a damaged file fails safe to keep all models), update drain keeps dispatching to idle nodes.
- **UI:** sidebar names this Mac and its place in the group; Music player card and row actions fixed; Hub reports each machine's current job; design refresh (type scale, spacing, status colours, one page scaffold, aligned tables).

### Gemma 3 12B 4-bit is shelved: it stays on the SSD, not on fleet Macs

Owner: Gemma 3 12B 4-bit (`gemma-3-12b-it-4bit`) is no longer carried by every Mac. It stays in the catalog, on the SSD kits and on any Mac
marked "keep all models", so it can still become a chat model later; it is just not copied to fleet Macs any more, and a fleet Mac that
already has it moves it into the self-cleaning Trash Folder (emptied after 15 days) the next time the Hub starts. One line in
`components/shelved_models.py`; the kit's copy step, the start-up sweep and the "do not fetch it back" rule all read that list.

### LTX-2.3 video is retired; the release ships no Video worker

Owner: LTX-2.3 is "bad quality, can't use it" - retired exactly like Lance (0.6.120) and ACE-Step 1.5 (0.6.116), by one line in
`components/retired_models.py`: `ltx-2-3-22b-ram-12gb-mlx` and `baa-ai/LTX-2.3-22B-RAM-12GB-MLX` (LTX-2.3 22B MLX mixed 2-8 bit, RAM-12GB).
- **Gemma is NOT retired** (coordinator, 2026-10-01: the owner may reuse it as a chat model). `gemma-3-12b-it-4bit` (Gemma 3 12B 4-bit,
  `mlx-community/gemma-3-12b-it-4bit`) stays in the catalog, kit listing and copy rules. Verified that nothing else used it as a text
  encoder: the only `requires_packages` in `model-manifests/` was LTX's, and `VideoModel.requires` (video generation.py) the only code
  reader. Chat is `qwen3-5-9b-4bit`; Music's `t5gemma` (Stable Audio 3) is a different checkpoint; Story/Gateway "gemma-4" ids are unrelated.
  It is still engine `video-ltx` (so it is registered and routes), but no studio runs that engine now, so it appears in no picker (the
  app builds pickers from the workers' catalogs) and a job for it is refused `STUDIO_NOT_RUNNING`.
- **What "retired" does, from the one list:** never offered (catalog, kit listing, `release_kit`, `model_ids`, staged manifests), refused by
  name (`MODEL_RETIRED`), dropped from the aggregate catalog a controller publishes even when a not-yet-updated node still reports it, skipped
  by `copy_models_for_this_mac.py` (which also prunes it from a Mac that has it), dropped from the model record.
- **No Video worker ships.** `stage_voice.stage()` ignores `--video-runtime` (NOTE printed) while the LTX package is retired, so the release
  manifest has no `video` component: no `runtimes/video` (**517 MB**, the installed app 4.1 GB -> ~3.6 GB), no `video_worker.py`, no
  `components/video`. A Video worker whose only manifest is retired would otherwise stop the whole Hub at start-up (`_video_spec`). The
  Hub reports no video model, no `video.generation` capability, and refuses a video job with a named code (never a 500, never queued).
  `components/video/`, `packaging/video-runtime/` and the LTX manifest stay in the repo, dormant, for a successor model; the manifest moved out of
  `model-manifests/` (so no catalog, count or doc sees it) to `components/video/retired/ltx-2-3-22b-ram-12gb-mlx.json`, and its capture row left
  `capture_model_manifests.py`.
- **Trap fixed on the way:** the YuE2 runtime (`runtimes/music-yue2`, the sung-song engine, ~698 MB) was staged INSIDE `_stage_video`, after its
  "no video runtime" early return - so a build without `--video-runtime` silently shipped Music with no sung songs. It is now staged by
  `_stage_music` and recorded on `components.music` (it was `components.video.yue2_runtime_*`; nothing else read it). Also fixed: the
  `--yue2-runtime` branch validated `video_runtime` instead of the YuE2 tree.
- **Updates keep working both ways.** `ReleaseInstaller.verifyDeclaredComponents` (native/UpdateClient.swift) checks only the components a
  manifest declares, and `runtimePaths` still lists `video`: an app accepts a release with or without Video (the 0.6.120 incident was a
  release that DECLARED `video` to an installer that did not know it). The 0.6.121 BRIDGE label (`'runtime': 'voice'` in `_stage_video`) and
  its test are untouched: today nothing declares `video`, so the label is dormant; keep it until no Mac runs < 0.6.121 in case Video returns.
- **Native app:** the Video page, with no video model published, says so plainly ("No video model is available...") instead of an empty
  picker and a dead Generate button; the Image-to-video blurb and "Manage Video Models" are hidden in that state. Earlier clips stay listed.
- **Kit/docs:** kit listing 28 -> **27 packages, ~84.4 GB** (LTX 19.1 GB; Gemma's 8.1 GB stays), kit README / SSD-KIT-README / NODE-SETUP-CHECKLIST / runtime-contract / model-manifests README, model record
  (`docs/model-benchmarks.md` regenerated; the LTX reading row removed, its evidence folders kept), HANDOFF stage command (no `--video-runtime`),
  `docs/tasks/video-lane-plan.md` header.
- **Tests:** `tests/test_video_retired.py` (real Hub app + the real catalog with no Video worker: package/catalog/capability routes, LTX absent,
  Gemma present, refusals `MODEL_RETIRED` / `MODEL_NOT_PACKAGED` / not-ready for Gemma; a release without `video` spawns the other five
  workers; installer table still knows `video`; Swift Video page state), `test_retired_models` (LTX ids + Gemma stays offered),
  `test_voice_bundle` (retired Video not staged, YuE2 still is). Mutation-proved: removing the two ids fails three tests.
- **A Mac sheds an installed retired model by itself:** new `retired_models.retire_installed()` (called at Hub start-up, beside the shelved
  models' step) moves any installed retired package's folder (`<library>/<package id>`) into the self-cleaning Trash Folder
  (`<data dir>/trash/<UTC date>/retired-models/`, purged after 15 days) - a same-volume rename, never a delete, a failed move is skipped and
  retried at the next start. It applies to every id on the list (so a leftover ACE-Step / Lance folder goes too); Gemma is not on it.
  (Before this, a retired model left on a Mac was only moved by the copy step's prune, into the library's own `_trash`.)
- **Left for the coordinator when this ships:** move LTX (17.81 GiB) from each SSD's `model-library/` (the 517 MB `runtimes/video`
  inside the old app copy goes away by itself with the app swap).

## 0.6.126 — 2026-10-01 · Long text-to-speech: even pauses between sections, short lines match their neighbours

### Long voice-overs: even pauses, and short lines no longer jump in volume

A long script is read in sections. The voice models left up to a second of near-silence at the start or end of each one, so the
gaps between sections were long and uneven (about a second where about a third of a second was meant). The Hub now trims that
empty stretch and puts in exactly the pause it is meant to have, for every voice model. A very short line in the middle of a story
(for example "Father, there is a boat out there!") is also nudged to the volume of the lines around it, instead of coming out
noticeably louder or quieter.

### Technical notes

**Cause (owner: "it's glaring"; audit `_orchestra/audit-20260930/tts-samples`, 300 joins, every engine):** a long script is rendered as
sections and joined by `_join_long_form_wavs` (`components/voice/app/backend/generation.py`), which wrote each section whole and then
appended the designed pause as exact zeros. Every engine returns a section with up to ~1.2 s of near-silence at its start and/or end
(-45 to -60 dBFS), so a designed 0.12-0.6 s pause was heard as median ~1.0 s (OmniVoice 0.98, VoxCPM2 ~1.0, Qwen3 1.7B 1.06, max 2.9 s)
and uneven from join to join. The one-line S4 section ("Father, there is a boat out there!") also came out 3-8 dB off its neighbours:
`loudnorm` cannot measure a ~1.5 s section well.

**Fix (all TTS engines, one place, after the existing `loudnorm` pass):** (1) each section is cut back to its speech: first/last run of
>= 30 ms of 10 ms windows above a threshold relative to the section's own level (95th-percentile window level - 33 dB, clamped to
-60..-40 dBFS), keeping 30 ms before and 60 ms after, with a 10 ms fade at each real cut; the inserted zeros are the designed pause
minus the pads kept either side, so the silence between two sections is the designed pause (unchanged values: engine join pause, or the
OmniVoice / Qwen 1.7B paragraph / sentence / soft sequence). Speech is never cut; a section with no speech-like run, or too short to
analyse, is joined untouched. (2) a section with < 4 s of voiced audio is moved toward the median voiced level of the sections within two
places, by at most +/-6 dB and ignoring < 0.5 dB; peaks the gain pushes past the -1.5 dB ceiling are rounded off by a soft knee, so
nothing clips. Single-section renders are not joined and are not touched.

**Measured on the audit's real clips** (the 36 of 40 long clips whose section sizes add
up to the file, re-joined from their own sections, 274 joins; `_orchestra/audit-20260930/join-fix/`): audible gap median 0.85 s -> 0.14 s (designed pauses 0.12-0.6 s), max 2.92 s ->
0.60 s; after-minus-designed median 0 ms, 95th percentile 30 ms, worst 60 ms. Short-section level step vs neighbours: worst 7.3 dB -> 2.0 dB.

Note for the Qwen3-TTS 1.7B Base audit contract: `private_edge_destructive_trim` stays `False` (speech is never cut); this only removes
near-silence the model itself rendered, as the 0.6B and every other engine's sections already were.

Tests (mutation-proved: 7 of 11 fail on 0.6.125's joiner): `components/voice/app/tests/test_long_form_join_edges.py`: pause equals the
designed one +/-20 ms at 0.12 / 0.3 / 0.6 s whatever the edges, per-join pauses, whole-render edges, speech samples preserved bit for bit,
no-speech section untouched, short-section gain (matched, bounded both ways, no clipping, no reference -> left alone).

## 0.6.125 — 2026-10-01 · The Hub's "free memory" matches what macOS can give; the 8 GB TTS runner measures held models

### A Mac that is fine is no longer told it is short of memory

The Hub used to count only part of what macOS can free up, so a healthy
8 GB Mac could have a voice job held for ever with "waiting for memory". It now counts what macOS can really give. The 8 GB voice test
can also measure a model the Hub would hold back (with a safety stop, putting the setting back afterwards) and recommends a number from what it saw.

### Technical notes

**Cause (real 8 GB Mac "1101", Hub 0.6.122):** the Hub held Qwen3-TTS 0.6B 8-bit ~11 min ("waiting for memory: needs ~3.2GB, ~3.0GB
free") on a Mac macOS itself called healthy (pressure normal, swap 0). `host_stats()["available_gb"]` was psutil's `available`,
which on macOS is free + speculative + inactive pages: it leaves out the file cache that is *active* (macOS gives it up on demand,
never to swap) and purgeable pages, so it ran 1-2 GB short of what a new job could really use.

**Fix:** one helper, `components/host_memory.py` `available_bytes()` = max(psutil's figure, free + file-backed + purgeable pages), less
the kernel's own free-page target (`kern.vm_page_free_target`), never more than total - wired - compressor; read from the kernel
counters (`vm.page_free_count`, `vm.page_pageable_external_count`, `vm.page_purgeable_count`, `vm.compressor_bytes_used`) with no
subprocess, and psutil's figure on any other platform or a missing counter. Used by `resources.host_stats()` (Hub gate, model health
"fits now", capabilities, metrics, peers), the Voice worker's `_memory_snapshot()` (preflight, "fits now" inventory) and its resource
telemetry, and the 8 GB TTS runner's records. The 3.2 GB requirement itself is unchanged. Note `memory_pressure`'s "free percentage"
(`kern.memorystatus_level`) is NOT headroom: it is (active + inactive + free + speculative) / total, so it stays at 87% on a 32 GB
Mac while free pages fall to near zero (measured by allocating 12 GB); it only drops as wired or compressed memory grows.

**Measure a model the guard would refuse** (owner: "do this along with the 8 GB TTS runs"): `PUT /api/hub/memory/admission`
`{model, min_free_memory_gb | null}` (and `GET ...?model=`) sets/clears the existing operator override
(`memory_admission.set_override`, which had no route). While one is set the Hub forwards it to the Voice worker as `memory_floor_gb`
(`Txt2SpeechBody`), and `GenerationManager._memory_preflight` uses it instead of its own size + headroom estimate, so the two guards
never disagree. `qualification/eight-gb-tts-run.sh`: a model held for memory is cancelled, then re-run with that model's requirement
lowered to `TTS8_RELAX_FREE_GB` (0.5) and a safety net (cancel if swap grows > 1.5 GB or pressure reaches urgent); the override is put
back after the model, on any exit (finally / atexit / SIGTERM / SIGHUP), and at the next start from `tts8-guard-restore.json` if a run
died. New `results.csv` columns `guard_relaxed`, `override_used_gb`, `measured_peak_gb`, `swap_delta_gb`, `hub_free_floor_gb`,
`verdict`; `summary.txt` recommends a requirement = measured peak + 0.25 GB, labelled as measured on that Mac. An older Hub without the
route is skipped as before.

Tests (each mutation-proved): `tests/test_host_memory.py` (recorded 32 GB Mac + synthetic 8 GB 1101-shaped / tight / wired-heavy
fixtures), `tests/test_hub_jobs.py` (override route + forwarding + clearing), `tests/test_tts_8gb_runner.py` (`MeasureAnywayTests`),
`components/voice/app/tests/test_priority_mlx_models.py` and `test_model_defaults.py` (worker honours `memory_floor_gb`; snapshot uses the helper).

## 0.6.124 — 2026-10-01 · Chatterbox is shelved; the 8 GB text-to-speech test (supersedes 0.6.123, never published)

### Chatterbox is shelved; a text-to-speech test for the 8 GB Macs

**Chatterbox is shelved.** Both Chatterbox models (4-bit and 8-bit), and the small helper they need, are still listed
and still work wherever they are installed, but the drives no longer copy them onto new Macs, and a Mac that has them
moves them into the Hub's Trash Folder (emptied after 15 days). A Mac marked "keep all models" keeps them.

**New: an 8 GB text-to-speech test.** On the kit drive, in the Technical folder, double-click `RUN-8GB-TTS.command` on
an 8 GB Mac. It reads the same two texts in the two voices with Qwen3-TTS 0.6B 8-bit, VoxCPM2 4-bit and OmniVoice
bf16, and writes a folder with the audio files and a short plain-English summary of what fits. It changes nothing on
the Mac.

(0.6.123 was never released; this includes everything in it.)

### Technical notes

0.6.123 was never published or installed anywhere; this release carries everything in it (relaunch safety net,
shelved models) and adds two owner-requested changes.

**A. Chatterbox disqualified = shelved** (owner: "chatterbox sucks ... we don't retire it completely, just stop
copying it ... both chatterboxes"). `components/shelved_models.py` `SHELVED_MODELS` gains `chatterbox-4bit`
(`mlx-community/chatterbox-4bit`), `chatterbox-8bit` (`mlx-community/chatterbox-8bit`) and their companion
`s3tokenizerv2` (`mlx-community/S3TokenizerV2`). Checked in every manifest: `s3tokenizerv2` appears in
`requires_packages` of the two Chatterboxes and nothing else, so it goes with them (left alone, the copy step would
still have carried it). Same semantics as the other shelved models: they stay in the catalog and work where their
files are; the kit's copy step skips them ("Shelved, not copied"); a Hub on a Mac not marked `keep_all_models`
moves an installed copy into its Trash Folder at start-up (15 days) and refuses to download it; the weights stay in
the SSD model library. **Mark the owner's own Mac keep-all BEFORE it starts 0.6.124**, or its Chatterbox 4-bit /
8-bit / S3TokenizerV2 folders move to the Trash Folder at that first start (recoverable for 15 days).
`tests/test_shelved_models.py` seeds and the Hub-sweep script now include the three; the existing "no kept model
requires a shelved one" test is what proves S3TokenizerV2 is safe to shelve.

**B. The 8 GB text-to-speech test** (`qualification/RUN-8GB-TTS.command` + `eight-gb-tts-run.sh` +
`README-8gb-tts.md` + `tts-script-S2.txt` / `tts-script-S4.txt`), the TTS twin of the 8 GB Z-Image run. Double-click
on the 8 GB Mac; it runs Qwen3-TTS 0.6B 8-bit, then VoxCPM2 4-bit, then OmniVoice bf16, each reading the same two
texts (S2 ~175 words, S4 ~3 minutes, byte-for-byte the 32 GB test's) in "Chris - Soft and Soothing" (`cd07b21a2ccf`)
and "Christian - Slow & Calming" (`0616aec33b41`), one job at a time, through the local Hub's own
`POST /api/hub/jobs` (modality `voice`) so scheduling, section splitting and joins are production's. Pre-checks, each
refusing in plain words and writing nothing: RAM (`hw.memsize`; non-8 GB refuses unless `--quick` /
`--allow-non-target`), Hub running and >= 0.6.122, the three packages `files_verified` + `eligible` and the two voices
present (via the Hub's own model-packages / model-health / voices views), and no active Hub jobs (waits up to 30 min,
re-checked before every clip). Per clip: status + error, wall time, audio seconds, RTF, peak `footprint` of the voice
worker process tree (every second), memory pressure, swap before/after/peak, responsiveness, and a flag when a clip
takes over 3x its 32 GB time. Writes a timestamped folder beside the script (Desktop if read-only) with the WAVs,
`results.csv` (the 32 GB `summary.csv` columns plus status, error, memory pressure, swap, RAM, macOS, app version,
chip, 32 GB reference, ratio ...) and a plain-English `summary.txt`. Changes nothing (no setting, install or delete);
keeps the Mac awake with `caffeinate` while it runs. The program is Python embedded in the shell file and run by the
installed app's own interpreter (no system Python needed). `--quick` = S2 in Chris's voice for each model.
Shipped through `.build/prepare-kit.sh` (copy + `chmod` + added to the Technical folder's managed set so the prune
keeps it). `tests/test_tts_8gb_runner.py` pins the ids against the catalog, the texts, the wrapper, and the kit step.
Verified on the 32 GB Mac against the real local Hub 0.6.122 (`--quick`, all three models: completed, S2 audio
68.4 / 78.8 / 68.08 s, equal to the 32 GB test's).

## 0.6.123 — 2026-10-01 · An update can never leave a Mac stuck; shelved models (never published; carried in 0.6.124)

### Updates can no longer leave a Mac stuck; a few big models are shelved

**An update can no longer leave a Mac stuck.** If the old app ever fails to close after an update, the update
now closes it for you after two minutes and opens the new one. Nothing to do.

**Some models are "shelved".** Whisper base, Whisper small, VoxCPM2 bf16 and Fish S2 Pro bf16 are still listed and
still work wherever they are installed, but they are no longer copied onto other Macs from the drives, and a Mac
that has one now moves it into the Hub's Trash Folder (emptied after 15 days). A Mac you mark "keep all models"
in its settings keeps them all. Whisper turbo is the subtitle model to use.

### Technical notes

Two owner-approved changes.

**A. Relaunch safety net** (owner: "can we remove that barrier?"). 0.6.122 fixed the hang; this makes sure no
future bug can strand a Mac. The helper `relaunchAfterUpdate` starts (`/bin/sh`, now `RelaunchHelper` in
`native/StudioHub.swift`) waits for the old app pid; if it is still alive after **120 s** it sends SIGTERM, waits
**10 s**, sends SIGKILL, then runs `open` on the new app. The normal path (the old app exits by itself) is
unchanged: no signal is sent. The helper is now spawned with `posix_spawn` in **its own session**
(`POSIX_SPAWN_SETSID`) with **no inherited descriptors** (`POSIX_SPAWN_CLOEXEC_DEFAULT`, stdio on /dev/null): the
login launch agent (`KeepAlive`, no `AbandonProcessGroup`) makes launchd kill what is left in the app's process
group when it exits, which would have taken the helper with it, and an inherited data-folder lock descriptor would
have kept the new copy out. Seam `spawnRelaunchHelper` now takes `(pid, app path)`.
`tests/native/lifecycle_main.swift` `relaunch-helper` runs the real helper against stand-in old apps: one that exits
by itself (opened, never signalled), one that never exits (SIGTERM), one that ignores SIGTERM (SIGKILL); each must
open the new app only after the old pid is gone, and the helper must be its own session leader.
Mutation-proved: no escalation, no SIGKILL step, no SETSID, kill straight away - each fails an assertion.
A Mac on 0.6.119-0.6.122 still relaunches with its own old helper when it takes THIS update (no safety net
until it is on 0.6.123).

**B. Shelved models** (owner, 2026-09-30). `components/shelved_models.py` (`SHELVED_MODELS`), beside
`retired_models.py`, seeded with Whisper base (`whisper-base`, `mlx-community/whisper-base-mlx`), Whisper small
(`whisper-small`, `mlx-community/whisper-small-mlx`), VoxCPM2 bf16 (`voxcpm2-bf16`,
`mlx-community/VoxCPM2-bf16`) and Fish S2 Pro bf16 (`fish-audio-s2-pro-bf16`,
`mlx-community/fish-audio-s2-pro-bf16`). Whisper turbo is not shelved.
- **Still in the catalog**, offered and usable wherever the files are. Manifests are still staged; the kit's
  model library on the SSDs keeps the weights (the simplest thing the kit tooling already supports: the shelf is
  "the library the copy step skips").
- **Kit copy step** (`qualification/copy_models_for_this_mac.py`): skips shelved models ("Shelved, not copied")
  unless the Mac is marked keep-all. It never removes one already on the Mac.
- **Hub**: at start-up, on a Mac not marked keep-all, an installed shelved model's folder is moved (same-volume
  rename; a failure is skipped) into the Trash Folder `trash/<UTC date>/shelved-models/<package>-<time>/`, which
  the Hub purges after 15 days. The row then reads `state: not_installed`, `shelved: true`, reason "Not on this
  Mac: ... is a shelved model ..." (never `invalid`); `dispatch_state` stays `ineligible`. `POST
  /api/hub/model-packages/{id}/download` and `/import` answer 409 with that reason for a shelved model on such a
  Mac, so nothing brings one back by itself (a moved one would be moved out again at the next start).
- **Keep-all device setting**: `keep_all_models` in `device_settings.json` (default **false**; read by the Hub and
  by the copy step). `PUT /api/hub/settings {"keep_all_models": true}` (Hub token) or add `"keep_all_models":
  true` to `~/Library/Application Support/StudioHub Standalone/hub/device_settings.json`. "Restore defaults"
  keeps it (it is about models, not output limits); a save that does not name it keeps it. **THIS Mac must be
  marked keep-all BEFORE it starts 0.6.123, or its Whisper base/small, VoxCPM2 bf16 and Fish S2 Pro bf16 folders
  move to the Trash Folder at that first start** (recoverable for 15 days, then purged).
- Whisper turbo is already the Subtitle default (`"default": true` in its manifest, the only one); the Gateway /
  Story selection of the subtitle model is separate.
- Tests: `tests/test_shelved_models.py` (list vs manifests, turbo not shelved and the default, no kept model needs
  a shelved one, keep-all parsing, copy step skip / keep-all / not pruned, Hub sweep / Trash layout / 15-day purge /
  not-on-this-Mac row / refusals / catalog / default false / survives restore-defaults). Mutation-proved (8 mutants).
- Bridge kept: the video component is still labelled `runtime: "voice"`.

## 0.6.122 — 2026-10-01 · Updates restart the app by themselves

### Updates restart the app by themselves

**Updates now restart the app by themselves** - the previous versions could hang half-quit after installing
an update (Force Quit fixed it). Studio Hub now stops its engine first, then closes and reopens on its own.

### Technical notes

Found in the 0.6.121 rollout: four controllers that took 0.6.119 -> 0.6.121 installed it and then sat
half-quit for ever (Force Quit, then open, fixed each). `AppDelegate.relaunchAfterUpdate` called `NSApp.terminate`
from the release tick, which is a main-actor task. With the engine running, `applicationShouldTerminate` answers
`.terminateLater` and AppKit waits in a nested loop; the engine's exit is delivered as another main-actor task,
which cannot run until the tick returns, so the reply never came, the app never quit and the reopen helper
waited on it for ever. Diagnosis: `_orchestra/audit-20260930/update-0120/diag/relaunch-*`.
- **Fix** (`554ed1f`): the relaunch stops the engine FIRST and quits from `onStopped` (`engineStopped`), so
  `NSApp.terminate` runs from the engine's exit, when nothing is running and `applicationShouldTerminate` answers
  `.terminateNow`. A hung stop is still force-stopped by itself after 15 s. No engine running: it quits at once.
  Seams `spawnRelaunchHelper` / `terminateApp`; `tests/native/lifecycle_main.swift` `relaunch-after-update`
  asserts the order (helper started once, no quit while the engine runs, quit after it stops, idle quits at
  once). Mutation-proved (terminate straight away -> the quit-while-running assertion fails).
- **Other paths audited:** no other `NSApp.terminate` runs inside a main-actor task. The user-Quit path
  (`applicationShouldTerminate`) is reached from a menu/Dock/Apple event on the run loop, not from a task; its
  task only replies (`NSApp.reply`), which is safe. The one remaining `terminate` is the launch-error alert in
  `applicationDidFinishLaunching` (no controller, `.terminateNow`).
- **Bridge kept:** the video component is still labelled `runtime: "voice"` (`0.6.121`). Macs still on
  0.6.119/0.6.120 verify an update with their own table (no `video`) and must be able to take this release. The
  bridge can only be reverted once no Mac runs below 0.6.121.
- **A Mac on 0.6.119-0.6.121 still has the bug when it takes THIS update:** its own (old) app does that one
  relaunch. After the install the new app is already in place; Force Quit the old process (or `kill` it) and
  open Studio Hub. From 0.6.122 on, updates restart by themselves.

## 0.6.121 — 2026-10-01 · Macs can install updates by themselves again

### Macs can install updates by themselves again

**Updates install by themselves again.** The installer was refusing any release that includes the video
engine, so Macs quietly skipped the last update and stayed where they were. This release is labelled so the
installed apps accept it, and from here on the app knows the video engine, so later updates install normally.
Nothing you made is touched.

### Technical notes

Found in the 0.6.120 rollout: no controller or node updated. `ReleaseInstaller.runtimePaths`
(`native/UpdateClient.swift`) had no `video`, so `verifyDeclaredComponents` refused, AFTER staging, every
release whose manifest ships Video with `runtime: "video"`. An idle Mac took the offer, staged it, failed the
check, set `autoUpdateFailedBuild` and never retried; nothing showed off the Mac. Diagnosis:
`_orchestra/audit-20260930/update-0120/diag/` (the real 0.6.120 bundle fails the real check; the same bundle with
the label `voice` swaps).
- **Fix** (`7031035`): `video` joins `runtimePaths` (`Contents/Resources/runtimes/video/bin/python3`). Tests: the
  real 0.6.120 `release-manifest.json` is a fixture and must pass `swapStagedApp`; the fake-app helper builds the
  real layout instead of skipping runtimes missing from the table (that skip kept the old tests green by
  construction); `video` joins the per-modality missing-worker loop; a Python invariant
  (`tests/test_installer_knows_shipped_runtimes.py`) checks every runtime `stage_voice.py` writes is one the
  installer verifies. Mutation-proved (table entry removed -> both fail).
- **BRIDGE** (`28ad677`): the apps already installed (0.6.119/0.6.120) check a staged update against THEIR table,
  which has no `video`, so they can never take a release that says `video`. For THIS release only,
  `packaging/stage_voice.py` labels the video component `'runtime': 'voice'` (nothing reads that label except the
  installer check; `_video_spec` reads `runtime_sha256`). Verified by running the real 0.6.119 installer source
  against the staged 0.6.121 app.
- **Reverting the bridge** (set `'runtime': 'video'` back in `_stage_video`, delete the BRIDGE comment and
  `test_bridge_release_video_is_verifiable_by_the_apps_already_installed`) is possible only once no Mac runs
  below 0.6.121. 0.6.122 keeps it (see below).

## 0.6.120 — 2026-09-30 · Lance video is retired; video is LTX-2.3

### Lance video is retired; video is LTX-2.3

**Lance is gone. Video is LTX-2.3.** The small Lance video model did not make good enough pictures, so it is
no longer offered, no longer put on the install drives, and no longer part of the app. The video model is
LTX-2.3 22B (with its Gemma text encoder) on Macs with 24 GB of memory or more. The app gets about 1 GB smaller
and the model kit drops from 30 to 28 model packages. Nothing you already made is touched.

### Technical notes

Owner: "the Lance one sucks and is terrible" - retired the way ACE-Step 1.5 was (0.6.116). Video is now
**LTX-2.3 22B MLX mixed 2-8 bit (RAM-12GB)** plus its `gemma-3-12b-it-4bit` text encoder, and nothing else
(it was already the default: `MODELS` lists it first and `MODEL_ID`/`DEFAULTS` read it).
- **Retired list** (`components/retired_models.py`): `lance-3b-video-bf16`, `mlx-community/Lance-3B-Video-bf16`,
  `lance-3b-processor-qwen2-5-vl`, `Qwen/Qwen2.5-VL-3B-Instruct`. Never offered, never staged into a kit or
  listed, refused by name (`MODEL_RETIRED`) even if an old manifest sits on a drive.
- **Removed** (moved to `_trash/0.6.120-lance-retire/`, never `rm`): both manifests; `lance_runner.py`; the
  `LANCE_3B` model, `run_lance`, `lance_runtime_python`, the Lance canvases/limits, `VideoModel.lance_steps`/
  `lance_cfg_scale`/`MemoryPolicy.relay_on_below_bytes` and the `video-lance` engine (from `VIDEO_ENGINES`,
  `model_packages.ENGINES`, the Hub `_ENGINE_ROUTE`, the video smoke); `packaging/lance-runtime/`; the
  `--lance-runtime` / `--unqualified-lance-runtime` stage flags, `LANCE_RUNTIME_SHA256` and the
  `runtimes/video-lance` tree (nothing else read it - Lance was its only user); the Lance capture rows;
  `qualification/lance-video-worker-run.py`; the Lance reading in `model-readings.json` (record regenerated).
- Kit/listing: 30 -> **28 model packages, ~103.5 GB** (kit README, `SSD-KIT-README`). The installed app
  loses `runtimes/video-lance` (~1 GB).
- Tests: the Lance pins became "Lance is not offered, LTX is the one video model and the default, a Lance
  manifest left on a drive is not a video manifest"; `test_retired_models` covers all four ids. Mutation-proved:
  re-adding a Lance manifest fails four tests; dropping the ids from the list fails the retired test.
- Qualification evidence for Lance (`qualification/lance-video-worker-results-20260927-first/`, the Lance
  section of `README-video-hardware-test.md`) is kept as history.

## 0.6.119 — 2026-09-30 · Quit no longer asks when nothing is running

### Quit just quits

**Quit no longer asks when nothing is running - it only asks if a job is in progress.** Choose Quit
(Command-Q, the menu or the Dock) while the Mac is idle and Studio Hub stops its engine and closes
straight away. If something is generating, transcribing or downloading, you still get the question, so a
job is never cut off by accident. Closing the window still keeps everything running in the background.

### Technical notes

Owner: "if no job is running, quit is quit. if have job, then we should ask." `applicationShouldTerminate`
(`native/StudioHub.swift`) used to raise "Quit Studio Hub and stop its engine?" whenever the engine ran.
Now it reads **fresh** active work (`NativeWorkspaceStore.activeWorkNow()`: refresh + memory policy, then
the same `activeWorkCount` the updater's drain uses; bounded to 3 s so a hung Hub cannot hold Quit) and
asks **only if that is above zero**. Idle: the engine stops and the app quits at once. Busy: the same
"Keep Running / Quit and Stop" question. An update relaunch (`relaunching`) never asks. Once Quit is
decided (answered or not needed) `forceStopWithoutAsking` is set, so a stop that hangs 15 s is force-stopped
by itself - no second "Force Stop?" dialog. The in-app Stop Engine / Force Stop buttons are unchanged.
Tests: `tests/native/lifecycle_main.swift` `quit` (idle, busy-keep, busy-quit, relaunch; the question is the
`confirmQuitWhileBusy` seam) and `api_main.swift` (`activeWorkNow` reads fresh). Mutation-proved.

## 0.6.118 — 2026-09-30 · A Mac that never goes idle still updates itself; a push no longer stalls the controller

### Updates now install by themselves even on a Mac that is always busy

**A busy Mac no longer misses updates.** Until now a Mac only updated itself when it had nothing at all to
do, so a Mac that was always generating never updated. Now, when an update is ready, a Mac waits up to
**10 minutes** for a quiet moment. If none comes, it stops taking **new** work (other Macs pick it up),
lets what it is doing **finish** - nothing is ever cut off - then installs, restarts and carries on. The
sidebar shows **"Updating after current work finishes..."** while it waits. Only one Mac in a group does
this at a time, so the rest keep working. If an install fails, the Mac goes straight back to taking work.

**Macs set up from a drive update on their own.** A Mac installed from a drive used to ignore updates
until someone opened the Connections page. It now trusts its own Studio Hub's update key the first time
and checks the next update against it, with nobody needing to do anything.

**No question nobody can answer.** If shutting down for an update hangs, Studio Hub now ends it by itself
instead of showing a "Force Stop" dialog on a Mac that nobody is sitting at.

**Faster updates for the controller.** Sending an update to a controller no longer makes it re-check
everything for every file it receives, so it stays responsive while the update arrives.

### Technical notes

The live pilot (gateway pushed 0.6.117 to controller 1500) showed two gaps that kept every Mac on the
old version, and a slow controller.

**Drain for update** (owner decision 2026-09-30; `native/StudioAPI.swift` `releaseTick`, new
`components/hub/backend/update_drain.py`). An unattended install needs `activeWorkCount == 0`, which a
Mac under continuous work never reaches. Now a Mac with a verified update waits `updateDrainGrace`
(**10 minutes**, default; `defaults write <bundle id> UpdateDrainGraceSeconds -int N`) for a natural idle;
after that it asks its Hub to **stop taking new work**, lets everything running finish (nothing is ever
killed), installs, relaunches, and takes work again.
- **Refusal code: `MACHINE_BUSY`** (409). It is the code the gateway already files as a location refusal
  (`_LOCATION_REFUSAL_CODES` -> `LOCATION_NOT_READY`, the job is re-routed to another Mac) and the broker's
  own "not available right now" (`_is_machine_reservation_refusal`: the item waits, never fails). Applied
  three ways from one flag: `broker.busy_machines()` names every machine while draining (the scheduler
  starts nothing, peer requests are refused, the chat/transcription lanes cannot take the Mac);
  `_submission_readiness` refuses a submission first.
- **Stagger: one Mac per controller group drains or installs at a time, the controller included.** The
  controller holds one in-memory "update slot"; a node asks for it (`POST /api/hub/fleet/update-slot`,
  fleet and node credentials) before its Hub drains. A controller too old to know the route, or
  unreachable, does not hold a node back. Controllers update first anyway (a node is offered a release
  only once its controller runs it), so in practice a controller claims the slot first.
- **A drain can never wedge.** It is a two-minute lease the app renews every 30 s (through the install
  too); the slot is the same. A failed install ends the drain at once and that build is not tried again;
  an app that dies, a Hub that restarts or a Mac that reboots simply lets the lease lapse.
- While draining, jobs that are only *queued* do not hold the drain open (they wait in the Hub's saved
  queue through the relaunch); running work still does.
- Visible: the sidebar says "Updating after current work finishes..." while an update is waiting or
  draining, and `GET /api/health` carries `update_draining` for the fleet view.

**The key is pinned on the unattended path too.** `releaseTick` returned at once when no controller key
was pinned, and the pin was only made when someone opened Connections, so a Mac installed from a drive
never updated. The tick now pins its own Hub's release-signing key on the first read (trust on first use,
the rule a node uses at join) and verifies the offer against it on the very next tick (10 s later, not 10
minutes): a key read at the same time as the artifact still proves nothing. A different key presented
later is refused, never replaced.

**Other things in the unattended path that needed a person, fixed.** The relaunch after an install stops
the services; after 15 s of a hung shutdown it raised "The local services are still stopping" with a
Force Stop button nobody was there to press, and the update never finished. A relaunch after an update
now force-stops by itself (`forceStopWithoutAsking`). Checked and fine: the manual Install's "jobs are
running" confirmation lives in the view, not in the installer; the quit prompt is already skipped while
relaunching; nothing in the path needs a window, the Connections page or a connection notice.
(`memoryInventory != nil` is still required: a Hub with no memory-policy route cannot say whether a Studio
is busy, so it waits - only a Hub older than the app answers that way.)

**A blob push no longer stalls the controller** (`components/hub/standalone.py`, `backend/app_release.py`,
`backend/release_delta.py`). Every `PUT /api/hub/release/blob/<sha>` recomputed the full missing set
(directory listing plus a walk of ~93,000 manifest entries) on the event loop just to fill in a "remaining"
count no sender reads. The PUT reply is now `{"ok": true, "stored": sha}` (gateway asks `GET /need` once at
the end); the disk and hashing work of a PUT runs in a worker thread; the installed app's sha index is
built once, not per blob served or pushed. `POST /api/hub/release` answers with an additive
`basis: {installed_app, wanted_blobs, needed_blobs}` so a push can say whether the controller measured
`need` against its own installed app (a controller that cannot find it needs the whole release).

## 0.6.117 — 2026-09-30 · A Trash Folder instead of deleting; one list of retired models

### Cleanup now uses a Trash Folder, old updates no longer pile up, and retiring a model is one line

**Nothing is deleted straight away any more.** When Studio Hub tidies up finished pictures, voices and
music, it moves them into a **Trash Folder** inside its own data folder instead of deleting them. The
Trash Folder empties itself: anything that has been there more than **15 days** is removed for good, and
if the disk gets short, the oldest trash goes first so it never stops you generating. **Storage** in
Settings now shows how big the Trash Folder is.

**Tidier defaults.** Studio Hub keeps **20 GB** of finished work for **3 days** and wants **5 GB** of the
disk left free (it used to be 50 GB, 30 days and 1 GB). A Mac that was still on the old numbers picks up
the new ones on its own; any number you changed yourself stays as you set it. The 20 GB counts only
your current work, not the Trash Folder.

**Updates clean up after themselves.** When an update replaces the app, the old copy goes into the Trash
Folder instead of being deleted, so you can go back for 15 days. The downloaded update files, which
used to stay on the disk for good (about 1.9 GB each), move to the Trash Folder once an update has
installed.

**Retired models stay retired.** There is now one list of models we no longer offer. A model on it is
never shown, never put onto a drive kit and never accepted for a job, even if an old copy of its files
is on a drive. ACE-Step 1.5 is on the list.

### Technical notes

Two owner decisions (2026-09-30) and two clean-ups of what an update leaves behind.

**Output cleanup moves work to a Trash Folder; it never deletes it** (`components/hub/output_storage.py`).
- Acknowledged output with no reader and no transfer is now **moved** to `<hub data>/trash/<UTC date>/<batch>/`
  (a same-volume rename) instead of unlinked. A move that fails skips the file; there is no fallback
  to a delete. `max_bytes` counts **live** output only.
- The Trash Folder empties itself: a dated folder older than **15 days** is removed (`purge_trash`, run
  at the start of every sweep, even with cleanup switched off). That purge is the only hard delete.
- **A full Trash Folder cannot wedge admission.** The Trash Folder shares the disk, so when free space is
  below the floor `admission()` first removes Trash folders **oldest day first**, only as many as it
  takes, and refuses only if the disk is still short - copies already scheduled for deletion go
  before work is refused. (Chosen over "refuse until day 16": a refusal for up to 15 days is the
  failure this prevents.)
- **New defaults:** 20 GB of live output, 3 days, 5 GB minimum free (were 50 GB / 30 days / 1 GB).
  A `device_settings.json` written before this release that still holds the old default for a value
  reads as "never chosen" and gets the new one; a value the operator changed is kept. Every save and
  restore stamps `defaults_version`, so choosing 30 days afterwards sticks.
- The storage screen shows the Trash Folder's size, "emptied automatically after 15 days"
  (`POST /api/hub/settings/preview` returns `trash: {bytes, days}`); the limits editor and the
  restore-defaults confirmation now say "moves to the Trash Folder".

**Updates no longer pile up on disk** (`native/UpdateClient.swift`, `native/StudioAPI.swift`).
- `swapStagedApp` moves the replaced app into the Trash Folder (`previous-app-<id>/Studio Hub.app`)
  instead of `removeItem`, so a rollback is possible for 15 days. On APFS the new app is clone-staged
  and shares blocks with it, so this costs about the changed bytes. If there is no Trash Folder or it
  is on another volume than the app, it falls back to the old `removeItem` (a move there would be a copy).
- Full-update downloads (`Updates/<key>/`, about 1.9 GB each) were never pruned. After **any completed
  install**, everything under `Updates/` - the download just installed and any older one - moves to the
  Trash Folder. A failed install moves nothing (the downloads are what the retry resumes from).
  `relaunchAfterUpdate` is unchanged.

**One list retires a model** (`components/retired_models.py`, `RETIRED_MODELS`, seeded with
`ace-step-1-5-turbo` and its model id). The Hub catalog (`_model_catalog`), the identity index
(`model_ids`), the aggregate model list published to the gateway (`_assemble_aggregate_catalog`), the
kit listing (`release_kit`), kit staging (`stage_voice.copy_model_manifests`) and the model-copy step
(`copy_models_for_this_mac.py`) all read it, and a retired model is refused by name
(`MODEL_RETIRED`) on submit and on peer dispatch. So an old ACE-Step manifest on a drive can never
bring it back. Retiring the next model is one line.

**Tests (each mutation-proved, failing on an assertion):** `test_output_trash` (prune moves, failed move
skips, purge only past 15 days and only dated folders, admission not wedged, defaults, old-default
migration), `test_retired_models` (adding an id removes it from the catalog, identity index, kit
listing, staging and published list, and refuses it), the model-copy end-to-end case, and the Swift
harness (old app in the Trash Folder after a swap, stored downloads moved after an install, a failed
install moves nothing, the fallback without a Trash Folder).

## 0.6.116 — 2026-09-30 · ACE-Step 1.5 is retired; music is YuE2 and Stable Audio 3

### ACE-Step 1.5 is retired; music is YuE2 and Stable Audio 3

**ACE-Step 1.5 is retired; music is YuE2 and Stable Audio 3.** The Music page now offers two models:
**Stable Audio 3 Medium (1.4B)** for instrumental music, which is the one it opens on, and
**YuE2 3B (8-bit AR + bf16 NAR)** for songs with sung lyrics. ACE-Step 1.5 Turbo is gone from the
model list, from the drive kits, and from the app itself, so the app is about 12 MB smaller and the
model library is about 7.7 GB smaller on a drive.

**Nothing changes for what you already made.** Music you generated with ACE-Step stays where it is.
Models you already copied to a Mac are not touched; the model step moves a model the kit no longer
carries into `_trash` the next time you run it, and never deletes it.

**This is the first small update.** Going from 0.6.115 to 0.6.116 copies the previous version and
downloads only the files that changed.

### Technical notes

**Owner decision (2026-09-30):** ACE-Step 1.5 Turbo (2B DiT) is not offered any more. The Hub's music
models are **Stable Audio 3 Medium (1.4B)** (instrumental, now the default) and **YuE2 3B (8-bit AR +
bf16 NAR)** (sung songs, with the 0.6.115 decoder fix).

- **Removed:** `model-manifests/ace-step-1-5-turbo.json` (to `_trash/`), so the drive kits and the
  Models list no longer carry it (catalog 31 -> 30 packages, 126.8 -> 119.1 GB, the 7.7 GB ACE-Step package);
  `components/music/app/backend/engine_child.py`, `generation.EngineExecutor`, `_validate_ace_step`,
  `_run`, the ACE source-tree verification (`source_tree_digest`, `engine_contract_reason`) and the
  now-unused `inspect_wav`; the ACE catalog entry, tier table, long-form strategy, checkpoints and
  family; `packaging/music-runtime/` (the ACE-only Python overlay, 1.7 MB); the staged
  `engines/ace-step-1-5` source (11 MB) and `--music-runtime` / `--music-engine` in
  `packaging/stage_voice.py`; `--engine-root` and the overlay check in `packaging/music_worker.py`
  and `_music_spec`.
- **Stable Audio 3 is the default music model:** `catalog.DEFAULT_REPO` (was `MAIN_REPO`, ACE) is what
  `generation.DEFAULT_MODEL`, the package cache and the worker's own manifest resolve. It is first in
  `/api/catalog`, so the app's music picker opens on it.
- **Music no longer has a runtime of its own.** Both engines already ran outside it (Stable Audio 3
  as a subprocess on the Voice runtime, YuE2 in `runtimes/music-yue2`); the overlay carried
  `vector-quantize-pytorch` and friends only for ACE (checked: every Stable Audio 3 module imports
  without it). The release manifest's `music` record is now `runtime: "voice"` with the Voice
  runtime's digest, which every deployed update client already accepts.
- **Nothing is resident, so the memory policy has nothing to release.** `/api/memory-policy` and
  `/api/memory/release` still answer the fleet contract; `loaded_models` is always empty.
  `/api/health` no longer reports `engine_loaded` / `engine_child_pid` (nothing read them).
- **A Stable Audio 3 job that fails after it was marked done** (a persist failure) now clears the
  record's artifact fields, as the ACE path did, instead of leaving an `output_url` for a deleted file.
- Docs and listings: `docs/model-benchmarks.md` (regenerated), `model-manifests/README.md`, the kit
  README, `SSD-KIT-README.md`, the node checklist, the runtime contract count and the R08 checklist.
- **Tests:** rewritten to assert ACE-Step is *not* offered and Stable Audio 3 + YuE2 are
  (`test_music_package_contract`, `test_music_memory_policy`, `test_memory_policy_contract`,
  `test_music_bundle`, `test_music_lane_contract`, `test_model_manifest_catalog`,
  `test_chat_packaging`, `test_activity_operations`, four hub-regression cases, the native fixture).
  The ACE-only tests (engine child, source digest, resident-child release) went with their code.
- **Rollout:** 0.6.115 -> 0.6.116 is the first delta update. Measured from the two stages' file
  manifests: **20 files changed (about 0.8 MB), 0 added, 798 removed (about 11.5 MB)** - the Music
  worker source, `hub_worker.py`, `music_worker.py`, `model_ids.py`, `memory_modes.py`, the Voice
  catalog, the release manifest and notes; the native binary and all six runtimes are byte-identical.
  The app is about 11.6 MB smaller (5,197,040,792 bytes of files down to about 5,185,460,000).

## 0.6.115 — 2026-09-30 · Scarcest-first scheduling, node removal from either side, delta updates

### Work goes to the right Mac, removing a Mac works from both ends, and updates get much smaller

**Jobs only go to Macs that can run them.** Studio Hub now checks that a Mac has enough memory for
the model before it sends it a job. When a Mac frees up, it takes the waiting job that the *fewest*
Macs could run, so your big Macs are kept for the work only they can do. If two jobs are equal, it
prefers the one that uses the model the Mac already has loaded, then the one that has waited longest.
**Send here** still wins on the Mac you chose, and chat and subtitles follow the same order. A Mac
that is busy is asked again within seconds instead of being left alone for half a minute, and the
capacity numbers you see count only the Macs that have enough memory. On a test of your fleet mix
(10 Macs with 8 GB, 5 with 16 GB, 2 with 24 GB, 1 with 32 GB, running 500 image, 200 voice and 100
chat jobs) no Mac sat idle while it could have worked, and each size of Mac was 97-98% busy.

**Removing a Mac works from either side.** Remove a node on the controller and the node leaves the
group within about a minute, or the next time it starts. Leave from the node and the controller
forgets it straight away; if the controller is switched off, the node keeps trying for up to 14 days.
A Mac the controller removed is told so, with the date. A Mac that left on its own can join again.
Machines are shown by their **name** everywhere (the ID appears when you hover), renaming a Mac
updates the controller, and a Mac that has **never connected since it joined** is flagged.

**Updates are much smaller.** Every build now carries a list of its files. A Mac that already has the
previous version copies it and downloads only what changed - for the last update that was 9 files and
13.5 MB instead of a 1.8 GB disk image - and if it cannot prove the result is exact, it falls back to
the full disk image. Nodes fetch the update from their own controller, and **a Controller now installs
an offered update by itself once it has nothing running or waiting**, just as a Node does. The kit
installer keeps the old app until the new one has been checked.

**YuE2 music works again on every Mac** - the decoder was installed but the Hub looked for it in the
wrong place.

**One thing to know:** going from 0.6.114 to 0.6.115 is still a full update, because the older app
cannot do the small kind. The first small update will be 0.6.115 to 0.6.116.

### Technical notes

Three reviewed branches (`wave2-sched`, `wave2-fleet`, `wave2-delta`) merged into one release.
**Rollout note: 0.6.114 → 0.6.115 is itself a full (disk image) update**, because 0.6.114 apps have
no delta path. The first delta update is 0.6.115 → 0.6.116.

**Scheduler** (`broker.py`, `memory_admission.py`, `chat_jobs.py`, `transcription_jobs.py`)
- **Memory is a hard eligibility floor.** A job only goes to a Mac with enough unified memory for
  that model. The requirement is, in order: measured → manifest → catalog
  (`memory_admission.requirement_gb`). A Mac below it is never a candidate, however idle.
- **A freed Mac takes the waiting job that the fewest Macs can run**, so big Macs are kept for the
  work only they can do. Ties: stay on the model already loaded, then oldest. **"Send here" wins on
  its Mac.** Chat and subtitle jobs follow the same order.
- **A busy Mac is retried within seconds.** The 30 s `MACHINE_BUSY` park and the 90 s avoid are gone.
- **Capacity numbers count only Macs with enough memory** for the model.
- Simulation (owner's fleet mix 10 x 8 GB / 5 x 16 GB / 2 x 24 GB / 1 x 32 GB; 500 image + 200 TTS +
  100 LLM jobs): 0 idle-while-eligible, 0 scarcest-first violations, 97-98% utilisation per tier.
- Tests: `tests/hub_regression/cases/test_scarcest_first_dispatch.py`; `test_memory_admission`,
  `test_hub_memory_admission`, `test_below_tier_warning`, `test_pool_peer_machine_busy` and
  `test_image_memory_pressure` rewritten where their premise changed.

**Fleet** (`fleet_nodes.py`, `auth.py`, `standalone.py`, `StudioAPI.swift`, `StudioViews.swift`)
- **Removing a node works from either side.** Controller remove: the node leaves within about a
  minute, or at its next start (it polls `GET /api/hub/fleet/nodes/{id}/status`). Node leave: the
  controller forgets it immediately, and the node retries the notice for up to 14 days if the
  controller is off.
- **A Mac the controller removed is told so, with the date** (a 403 carrying the removal detail
  instead of a bare 401). A Mac that left on its own can re-join.
- **Machines are shown by label everywhere** (the id is a tooltip); renaming a node updates the
  controller. A node that has **never connected since it joined** is flagged. The node id is
  persisted on first run.

**Delta updates** (`file_manifest.py`, `release_delta.py`, `release_sync.py`, `app_release.py`,
`UpdateClient.swift`, `install_app.command`, `packaging/stage_voice.py`)
- **Every build carries a per-file manifest** (`Contents/Resources/file-manifest/`). Controllers
  accept **manifest-only releases** and ask only for the files they are missing (`.../release/need`,
  `.../release/blob/{sha256}`); the signed descriptor commits to the manifest.
- **A Mac stages an APFS clone plus the changed files** (measured 0.6.113 → 0.6.114: 9 files,
  13.5 MB) and **falls back to the disk image** whenever it cannot prove the result.
- **Nodes pull their controller's release** (trust on first use of the publisher key).
- **Controllers auto-install when drained** — the 0.6.114 role gate is gone, so a controller with
  nothing running or waiting now updates itself like a node (this reverses 0.6.114's "controllers
  stay manual"; the sidebar Update badge remains).
- **The kit installer keeps the old app until the new one is verified.**
- Wire contract: `_orchestra/audit-20260930/CONTRACT-hub-release-delta.md`.

**Music**
- **YuE2 music works again on every Mac.** Every YuE2 job failed with *"the YuE2 decoder (yue2-vae)
  is not installed"* although the decoder was installed and verified. `_run_yue2` read a `requires`
  key from the generator's preflight report; that report has never had one, so the lookup was `None`
  everywhere. The decoder's folder now comes from the decoder package's own verified receipt
  (`MusicCache.companion_root`, from its manifest's layout destination), and the dead `requires` read
  and `_companion_root` are gone. Pinned by `DecoderResolvesFromARealLayoutTests` in
  `tests/test_music_yue2.py`, which builds the library the way the installer does from the two shipped
  manifests (no hand-written `requires`); reverting the fix fails it with the owner's error text.

**Merge note.** `auth.py`: both fleet and delta added allow-list entries; all are kept (fleet's
`/fleet/nodes/{id}/status` in `FLEET_ALLOWED` and `NODE_ALLOWED`; delta's release files/blob/need in
`FLEET_ALLOWED` and the five read-only release routes in `NODE_ALLOWED`).

## 0.6.114 — 2026-09-30 · The app says what the Hub already knew

### The app now tells you what it already knew

**A waiting job says why it is waiting.** Instead of *"Waiting for a free worker"* it shows the
reason — for example that the model is not downloaded on the Mac it was sent to — and, when it is
trying again, when. Every job row also says **which Mac ran it**, and a **failed job has a Retry
button**.

**The Fleet page tells the truth about each Mac.** A Mac that answers but has no model it can run is
no longer a green tick: each row says how many models are ready, how many need a download and how
many are blocked. Unreachable Macs come first, the page shows *as of* when it last read, a spinner
while it refreshes, and if something could not be read it says *what* — and keeps the last list
instead of going blank. **Send here** is now visible on the create pages ("Sending to … · Clear"),
and cannot be set on a Mac that is off. **Hand over…** asks you to confirm.

**Updates.** *If this Mac is a Node,* it now installs an offered update **by itself when it has
nothing to do** — no running or waiting jobs, no downloads — and then reopens Studio Hub so the
version shown is the new one. A busy Node waits until it is idle. **A Controller still updates only
when you press Install**, but the Connections entry in the sidebar now shows an **Update** badge.
Pressing Install while jobs are running asks first and says how many will stop. Installing no longer
freezes the window.

**Model downloads show progress** (a bar and "3.2 GB of 12 GB"), show the **real reason** when they
fail, and the failed row stays until you dismiss it.

**Storage.** Restore Defaults asks first, and the limits editor shows how much finished work cleanup
would delete with the numbers you typed.

**Smaller:** Overview's "In progress" now includes Video and Subtitle jobs; the sidebar counts the
models this Mac can actually run; "Voice" is not listed twice; an idle Mac polls less often, and a
hidden window does not poll at all.

### Technical notes

Twelve findings from the native-app audit, all of the same kind: the Hub published the fact and the
app drew a fixed sentence, a green tick or nothing. No routing or scheduling behaviour changes.

**Jobs**
- **A queued job shows its reason.** `governor_note` (and `tries` / `retry_at`) are decoded onto
  `NativeJob`, so *"'X' not downloaded on <machine>"* replaces the hard-coded *"Waiting for a free
  worker"*, and a job in the 30 s `MACHINE_BUSY` retry loop says *"retrying in N s (attempt 2)"*.
  The note used to tell the operator to *"broadcast the download"* — no such action exists — and now
  says *"download it on that Mac, or pin another"* (`broker.py`, a string only).
- **A job row says which Mac ran or holds it** and how many tries a failure took; **failed jobs get a
  Retry button** (`POST /api/hub/jobs/{id}/items/{i}/retry` already existed). A retry drops the batch
  from the finished-job cache.
- **Overview "In progress" includes Video and Subtitle jobs**, which it read as idle.

**Fleet**
- **Health is runnability.** The Fleet page reads `/api/hub/model-health` and shows per machine
  *"N models ready · N need download · N blocked"*; a Mac that answers but has no ready model is
  orange, not green, and `degraded`/`down` are orange instead of the neutral clock. The sidebar
  counts models that can *run* (`runsHere`), not models the Hub merely ships a package for. `speech`
  and `voice` no longer both render as "Voice", and modalities with no completed work are not listed.
- **"Send here" is visible where it applies**: a *"Sending to X · Clear"* chip beside Generate on the
  Image, Music, Video, Audio and Subtitle pages (orange when the pinned Mac is offline), and the
  button is disabled for an offline Mac (clearing an existing pin always works).
- **The Fleet page is a dated report**: *as of hh:mm:ss*, a spinner while Refresh runs, unreachable
  Macs first, and a failed read keeps the last good list and names what failed instead of wiping it
  and saying *"unavailable in this build"*.
- **"Hand over…" asks first**, like rotate, leave and remove.

**Updates**
- **Owner decision, 2026-09-30: a joined node updates itself when idle.** Every ten minutes the app
  reads the controller's offer (it used to be read only when Connections was opened). A node with
  nothing running or waiting — its own jobs, downloads, transcription, and every Studio's own `busy`
  flag, which is where a job sent by the controller shows — downloads, verifies and installs it, then
  **relaunches the app** so the version label is current. A busy node waits (checked every minute).
  Work that arrives while the file downloads defers the swap. A failed unattended install is not
  retried for the same build until the app restarts. **Nothing is read or installed unless a
  controller key is already pinned**, so this can never be the read that pins one; verification is
  unchanged.
- **Controllers stay manual** but the sidebar Connections row carries an *Update* badge.
- **A manual Install asks first when jobs are active**, naming how many will stop.
- **The install no longer freezes the window.** SHA-256 over the DMG, `hdiutil attach` and the 5 GB
  copy ran on the main actor; they run detached, the second hash of the same file is gone, and the
  progress line repaints ("copying the app, which takes a minute or two").

**Model downloads**
- **Progress is real.** The downloader prints nothing while it works, so the Hub measures
  `bytes_done` from the staging folder it writes into (`.part` files included) against `total_bytes`,
  the manifest's own sum; the row draws a bar and *"3.2 GB of 12 GB"*.
- **A failure shows the downloader's own words** (`error_tail`, the last 300 characters of its
  output), not *"Hub model download error."*, and stays on the Models and Overview pages with a
  Dismiss button. `recovery_pending` is drawn.
- **Finished runs are pruned** from `native-model-downloads.json` and no longer polled every two
  seconds for ever, and **one failed read no longer drops a running download** — only the Hub's 404.

**Poll cost**
- Failed batches are cached like finished ones, **but only on the Hub's positive word that nothing in
  the batch is waiting or running** (a Retry makes it active again). Polling is 2 s only while work
  is active and the window is visible, otherwise 10 s, and is skipped while the window is hidden or
  minimised — the update check is not, because a node's window is normally closed.
  `/api/hub/jobs` has no `since`/limit parameter, so it still lists every batch; that is Hub-side
  and was not invented here.

**Storage**
- **Restore Defaults confirms**, naming the limits it replaces. The Hub's `preview()` now returns
  `would_delete_bytes` — the sweep's own arithmetic replayed on the index without touching a file —
  and the limits editor shows *"Saving lets cleanup delete about X GB"* for the values typed.

**Tests:** `tests/test_hub_ops_visibility.py` (download progress and failure text, the storage dry
run proven equal to what `enforce` actually frees, the note wording), `tests/test_native_operator_surfaces.py`
(view wiring), and about forty new checks in `tests/native/api_main.swift`. Two existing tests were
rewritten because their premise changed and both said so: the download tripwire ("the Hub has no
byte counts") and the job-cache test, whose comment already named the native retry invalidation as
owed.

## 0.6.113 — 2026-09-30 · Why a node did not answer, as a field rather than a sentence

### A machine that is switched off now says so, instead of blaming Studio Hub

Nothing changes on screen in this release. It exists so that the **Fleet page can tell you the
right thing to go and check** when a Mac does not answer.

Two different things look identical when a Mac is unreachable, and they need opposite responses:

- **The Mac is on, but Studio Hub is not running on it.** Then the fix is to start Studio Hub.
- **The Mac is asleep or switched off** (or a firewall is blocking the port). Then there is nothing
  wrong with Studio Hub at all — the Mac needs waking.

This Hub has always told the two apart internally. It just never *said which* in a way the gateway
could read, so the gateway had to guess — and it guessed "Studio Hub", which is wrong for a Mac that
is simply off. That is why eight powered-off Macs could look like a Hub fault.

The reason is now published as its own field, so the Fleet page can say **"Check that Mac is
switched on and awake"** for a Mac that never answered, and **"Start Studio Hub on that Mac"** for
one that answered without its Hub. Nothing else changes, and no work is affected either way.

### Technical notes

**The distinction was always made; it was just not readable.** The peer poll separates two
failures that need opposite responses:

- **`no_hub`** — TCP refused. Something answered the network and nothing is listening on `:47873`,
  so the Mac is up and its Studio Hub is not running.
- **`unreachable`** — packets dropped. A firewall is blocking the port, or **the Mac is asleep or
  off.** Nothing about this says the Hub is at fault.

`peers.py` documents both. But a reader could not *use* the distinction, because the two code paths
that produce an unreachable node spelled it differently:

| Path | What it wrote |
| --- | --- |
| `_from_snapshot` (the peer poll) | `could not reach the node (no_hub)` — the token is there |
| `probe` (a direct dial) | `could not reach http://…: ConnectTimeout` — **no token at all** |

So the only way to learn the reason was to parse a sentence that is written two different ways.

**That is how the Gateway came to tell the owner to "fix Studio Hub on that Mac" for eight Macs
that were simply switched off.** The advice is right for `no_hub` and wrong for `unreachable`, and
with no field to read the Gateway had to give one answer to both.

`reason` is now published on an unreachable node by both paths, in the peer poll's own vocabulary.
`detail` is unchanged — it is still the sentence an operator reads.

**Also published for the same reason:** the direct-dial path classifies with the same rule the poll
uses (`httpx.ConnectError` is a refusal, everything else is a drop), so the two agree by
construction rather than by coincidence.

## 0.6.112 — 2026-09-30 · Two corrections 0.6.111 shipped, both found by auditing it

### Nothing changes for you in this one — it fixes two things 0.6.111 got wrong

**You do not need to do anything differently.** This release has no new behaviour and nothing to
learn. It corrects two mistakes inside 0.6.111, both found by going back over that release rather
than by anything going wrong in use.

**One was a check that looked stronger than it was.** The test that makes sure the update file on
each drive is actually runnable had been loosened to accept any `chmod` starting with `+`. That
meant `chmod +r` — which makes the file readable, not runnable — would have passed a test whose
whole purpose is to catch a file you cannot double-click. It now works out the permission properly
instead of matching the first character. The drive file itself was correct the whole time; only the
check was weak.

**The other was a comment that promised more than the code delivers.** The note explaining how long
a job waits for a busy Mac claimed the stall detector was one of the limits on that wait. It is not
— the stall detector is deliberately there to *show* you a quiet job, never to cancel one. The
comment now says plainly what is true: **a job waiting for a busy machine waits until that machine
frees up or you cancel it**, exactly as it did before. Nothing about the waiting changed; the
description of it was just wrong.

**Why bother shipping this?** Because a check that quietly accepts the wrong thing, and a comment
that describes a safety net that is not there, both get trusted later. You asked for the full cycle
on every change, and this is that.

### Technical notes

Both of these are in 0.6.111's own footprint. Neither changes a shipped behaviour; one closes a
hole in a guard, the other stops a comment claiming a guarantee the code does not provide.

**The repaired kit test still had the hole it was written to close.** `0.6.111` correctly stopped
pinning the literal `chmod +x` — `prepare-kit.sh` deliberately uses `755` — but the repair waved
through *any* mode beginning with `+`:

```python
if mode.startswith("+"):
    continue        # `chmod +r` passed a test whose message says the operator must run the file
```

`chmod +r "Update this Mac.command"` would have passed, on a file the operator then cannot
double-click. The mode is now parsed rather than pattern-matched: numeric modes are checked by the
owner-execute bit, and symbolic clauses are resolved (who the clause addresses, `+`/`-`/`=`, and
whether `x`/`X` is among the permissions). `a` counts as addressing the owner, `g` and `o` do not.
Pinned by a table of 21 spellings and **proved** by mutating the script to `644` and to `+r` — both
now fail with the reason named.

**The deferral comment named a bound that is not one.** 0.6.111's `MACHINE_BUSY` branch closed with
*"the batch's own lease, cancellation and the stall detector stay the bounds on how long this
waits."* The stall detector is the one thing it names that is **explicitly non-destructive** —
`broker.py:1641` says *"Visible, never destructive: nothing is cancelled on the strength of this"*,
and `capacity_wait_started_at`, which 0.6.111's own capacity branch writes, is read nowhere in the
tree.

The comment now says what is true: **nothing in the broker bounds this wait.** `tries` is restored on
every refusal so the `MAX_TRIES` ladder cannot end it, and cancellation is the operator's — the same
unbounded-queue semantics as the capacity wait directly above, and the same thing the owner asked
for (*"should the work get queued instead of failing outright?"*). It is a queue with no clock, not
a wait with a deadline, and the comment now says so instead of implying otherwise.

**Why this shipped at all:** 0.6.111 was audited independently, from the artefacts rather than from
its own summary. Both defects are in things that audit added or repaired, and both are the same
class the project keeps paying for — a guard that reads as stricter than it is, and a comment that
reads as a guarantee the code never made.

## 0.6.111 — 2026-09-30 · A Mac that is reserved on the far side is a wait, not a verdict

### A busy Mac now makes your work wait, instead of failing

You asked the right question about a job that died:

> *Should the work get queued instead of failing outright?*

**Yes. That's what it does now.** If a Mac in your pool is reserved by something else at the moment
your work arrives, the job no longer **fails**. It **waits**, and then it runs — on that Mac if it
frees up, or on a different free Mac if another one opens first.

Before this, the first thing that happened was a failure, and it happened even when the rest of the
pool was sitting idle:

```
WORKER_TERMINAL_ERROR — HTTP 409: local is reserved for other work on this Mac
```

The waiting is deliberate. A reservation is temporary by nature — the work holding it finishes, or it
expires — so it's a wait, not a rejection. **No attempt is used up by waiting**, and nothing is
generated twice.

### Two things are still genuine failures, and still fail loudly

Some refusals aren't temporary, and we won't pretend they are:

| What the Mac said | What happens |
| --- | --- |
| The **model is paused** | Fails — retrying elsewhere won't fix a decision someone made |
| The **Mac or Studio is switched off** | Fails — same reason |

Those stay as errors, with the reason attached.

### The waiting message now tells you what's actually happening

While your work waits for a free Mac, the Hub used to say:

> *"Waiting for a Voice Studio where the selected shared voice is synchronized"*

**— even for an image or video job.** It now says it's waiting for a free machine, and why that
machine isn't available (reserved by other work, or very recently failed). The Voice message still
appears where it belongs: a voice clone that really isn't synchronized yet.

### Nothing changed about the Mac at the other end

The refusal itself — the `409` that says *"this Mac is busy"* — is unchanged and stays that way.
This is a different Hub learning to **wait** instead of **give up**. Your other Macs need nothing.

### Technical notes

**Reported from the Gateway:** a batch submitted with `routing: "pool"` failed its *first* item,
terminally —

```json
{"item_index": 0, "code": "WORKER_TERMINAL_ERROR",
 "message": "HTTP 409: local is reserved for other work on this Mac"}
```

— while the rest of the pool sat idle. The owner's question was the right one: **"should the work
get queued instead of failing outright?"** Yes, and now it does.

**It was a classification error, not a scheduling error.** `_eligible_studios` already refuses to
hand an item to a machine in `busy_machines()` — that filter was never the problem. But
`busy_machines()` is **this Hub's own lease table only**, and a *peer's* reservation can never be in
it: the controller's row for a node uses `machine` = **node id** (`fleet_nodes.py:498`), the serving
node's own worker row uses `machine="local"` (`packaging/hub_worker.py:344`), and the peer publishes
**no busy fact** — `/api/hub/resources` carries host and per-studio up/down only. So the 409 at the
door is the **first** place the controller can learn a peer is reserved. Pre-filtering it is
impossible today without a new published field.

**The same condition was two different things.** Inside one Hub, a lease is a *skip*. Over the peer
surface it was a *terminal failure*. A transient lease was reported as a verdict — and with every
machine busy, every item in the batch died on its first 409.

**Two fixes, and the second is why the first was invisible.**

1. `_worker_http_error` read the code only from *inside* `detail` (the worker's shape:
   `{"detail": {"code": …}}`). The peer surface puts it *beside* it — `standalone._peer_refusal_response`
   writes `{"detail": "<reason>", "code": "MACHINE_BUSY"}` — so **every peer refusal arrived
   untyped** and landed as `WORKER_TERMINAL_ERROR` naming no cause at all. It now reads both shapes.
2. `MACHINE_BUSY` gets a **deferral** branch: `state="queued"`, `retry_at` +30 s, the machine avoided
   for 90 s so the pool offers the work elsewhere, and `tries -= 1`. **The refusal arrives before
   this item's worker saw the request**, so no attempt is spent and no duplicate is possible.

**The code is required, not just the 409.** A 409 is also how a peer names an operator decision no
other Mac can fix — `MODEL_PAUSED`, `MACHINE_DISABLED`, `STUDIO_DISABLED` — and those stay
**terminal**. Retrying those elsewhere cannot help, so they must not be laundered into a wait.

**The peer side is untouched.** `409 MACHINE_BUSY` is the pinned wire contract
(`tests/test_studio_proxy.py:366-403`); what changed is that this Hub now waits instead of dying.

**Also fixed, and it was going to get much more visible:** when every eligible machine is inside a
temporary avoid window, the dispatcher wrote *"Waiting for a Voice Studio where the selected shared
voice is synchronized"* — **for any modality**. `_shared_voice_allows_studio` returns `True` for every
non-voice modality, so for an image batch the avoid window is the *only* thing that can reach that
line. The note now distinguishes the two waits and names the machine when it is an avoid window.
Shipping the deferral without this would have made a wrong sentence the ordinary outcome of every
pool dispatch.

**Verification.** Gate: `unittest discover -s tests -p 'test_*.py'` → **1519 tests, OK (12 skipped),
606 s, exit 0**. The new cases in `tests/hub_regression/cases/test_pool_peer_machine_busy.py` are
**mutant-proved four ways**, each failing with a clean assertion rather than an error: reverting
`broker.py` to `HEAD` (2 failed — `error` vs `queued`, and `WORKER_TERMINAL_ERROR` vs
`MODEL_PAUSED`), disabling only the code-read (2 failed), removing only the deferral branch
(1 failed, reproducing the reported message verbatim), and restoring only the old voice-only note
(1 failed, reproducing the misleading sentence verbatim).

**A second red test was found and was *not* mine.** The gate's first run failed
`test_the_kit_ships_it`: it pinned the literal `chmod +x` in `prepare-kit.sh`, which was
deliberately changed to `chmod 755` in 0.6.110 (`+x` adds the execute bit to whatever mode the file
arrived with, so a source written 600 landed on a drive as 711 beside 755 neighbours). **The script
was right and the test was stale**, introduced by the previous release and left red. The assertion
now checks the property it was written to protect — that the mode grants its owner execute — and was
**proved to fail** when the mode is mutated to 644.

## 0.6.110 — 2026-09-29 · One command to update a Mac, and it skips the app copy when it can

### One file to update a Mac, and it stops copying the app when it doesn't need to

You asked whether it still copies the whole app when the app is already current, just to add a
model. **It did — every time.** About 5 GB copied, the old one deleted, the new one moved in, plus
the password prompt, for a Mac that already had that exact build.

There is now **`Update this Mac.command`** on the kit. Double-click it and it:

- **Compares the two builds first.** If this Mac already has the exact build the kit carries, it
  says so and **skips the copy** — that's the slow part, and there's nothing to gain from it.
- Otherwise installs the app, **then** copies the models, one after the other, without asking you to
  press Return in between.
- If it did have to install, it tells you which it is doing, so a skip never looks like a failure.

**Don't start `1` and `2` at the same time**, though — one file is still the answer there. `1`
replaces the app, and `2` runs using the app it just replaced, so started together `2` would either
find no app or work from files that are being deleted underneath it. The new file does them in the
right order and waits, which is the part that makes it safe.

`1 - Install the app` and `2 - Copy the models` are still there and unchanged, for when you want to
look at one step on its own.

### Technical notes

**The owner, on updating a fleet:** *"what if the app is current and i'm only adding the model
weight, will we still copy the app and replace it? i mean it's a waste of time still. or that's
already properly guarded?"*

**It was not guarded at all.** `1 - Install the app.command` copied the whole bundle on **every**
run — quit the app, copy ~5 GB to a staging folder, delete the installed copy, move the new one in —
with nothing comparing the two builds first. On a fleet where most Macs are already current, that is
most of the work for none of the result, and it is the part that needs an administrator password.

**`Update this Mac.command`** runs the two steps in order:
1. Reads each bundle's own `release-manifest.json` — **version *and* `implementation_commit`** — and
   **skips the copy when they match**, saying so.
2. Otherwise installs the app, then copies the models. No `Press Return` between them.

**Version alone would not do.** Two builds can share `0.6.110` and differ, which is exactly what a
rebuild of the same release is; the commit is what makes the comparison mean "this build", and a
test stubs the commit read out to prove the guard fails without it.

**The identity is read with `plutil`, not Python** — deliberately, because the interpreter this
would otherwise borrow lives *inside the app being replaced*, which is the one thing that may not
be there while the comparison is being made. And an unreadable or missing manifest **installs**
rather than skips: a skip is only ever the result of both identities being present and equal.

**Why the two steps cannot run beside each other** — the other half of the question he asked. `1`
replaces `/Applications/Studio Hub.app`; `2` runs its helper with the Python from inside that same
app. Started together, `2` would either find no app and fall back to the copy on the drive — which
fails if the drive is unplugged — or keep running from files `1` has just deleted. So this runs them
in order and waits. **The waiting is the feature**, and a test asserts neither is started in the
background.

**Two launchers learned to be driven.** `install_app.command` stops twice for a person — which
Applications folder, and reading the result — and honours `STUDIOHUB_UNATTENDED=1` to skip both. The
model copy's *"press Return to start"* is answered by `STUDIOHUB_ASSUME_YES=1`, an explicit variable
rather than a piped newline, because a pipe would also answer any *future* prompt added there,
silently. Running either file on its own is unchanged and still asks.

Both numbered commands remain, unchanged in behaviour, for when a step needs looking at alone.
`12 tests` in `tests/test_model_copy_script.py`, one **proved to fail** with a version-only
comparison. **Gate: 1519 tests, OK (11 skipped).**

## 0.6.109 — 2026-09-29 · A controller that booted before its network was unreachable, and looked fine



### The controller no longer comes up unreachable after a restart

This is the one you called a terrible bug, and you were right. Two things you saw were the same
fault.

**Nothing blocked your IP** — there is no blocking, no lockout and no rate limit anywhere in Studio
Hub. What happened is that the Hub reads this Mac's network addresses **once**, at the moment it
starts. After a power cut a headless Mac starts its login items *before* the network is up, so its
own address is not there yet — and Studio Hub read that as "this Mac no longer has that address" and
started on loopback only.

On loopback the Hub answers its own health check perfectly. It said **"Local services ready"** while
no machine on the network could reach it. That is why clicking stop and then start fixed it, and why
it seemed like luck: by then the network was up, and the same reading came back with the address in
it.

**The offline nodes were the same fault.** A controller listening only on loopback cannot check on
its nodes either, so they showed offline — not because they were, but because it could not ask.

Now:

- **The Hub remembers which addresses were missing and restarts itself the moment one appears.** You
  do not have to stop and start anything, and it does not narrow permanently.
- **The message tells the truth.** It used to say this Mac *no longer has* that address, as a fact,
  from one reading. It now says it does not have it **yet**, that this is normal for a minute after a
  restart, and that the services will restart on their own once it arrives.

If you ever do see it, the honest reading is: **the controller is up, and it is not reachable from
the network yet** — not that a node did something wrong and not that an address is banned.

### Technical notes

**The owner, after a power cut:** *"the controller is headless. It was off then it turned it self
back on with the restart and the backend thing. But I tried to connect to it from the node. It
doesn't work I think the ip was blocked. I fiddled around for a bit then I just just stop server and
then start server again. Only then the node can be connect… I have to restart it every time there's
a new node. this is a terrible bug."*

**Nothing was blocked — there is no IP blocking anywhere in the Hub, no lockout and no rate limit,
and no per-IP state at all.** What happened is worse and simpler. `bindableListenHost()` reads this
Mac's addresses **once, at the instant the services start**, and treats a chosen address that is not
there as one this Mac *no longer has*, dropping it and binding whatever remains — **loopback, if
nothing else is left**. On a headless Mac after a power cut that instant is the worst one available:
the app is a login item, so it starts while Ethernet is still negotiating, DHCP is unanswered and
Tailscale has not come up.

The Hub then came up on `127.0.0.1`, **answered its own health check, wrote its ready file, and
reported "Local services ready"** — while being reachable by no node on the network at all. That is
the whole defect: a service that is healthy by every measure it publishes and unreachable by the only
measure that matters. His manual stop/start was not superstition, it was the same read taken a minute
later, after the network had arrived.

**The second symptom is the same cause.** A controller bound to loopback cannot probe its nodes, so
they showed offline until he restarted it — *"after I've connected the machines, it's still shown
offline, until I click restart or sth."* One cause, both symptoms.

Two changes:

- **A missing address is remembered, not accepted.** `addressesMissingAtStart` keeps the set, and the
  three-second supervisor loop restarts the services the moment one of them appears. The spawn clears
  the set, so a Hub the operator has *deliberately* narrowed is restarted at most once, never in a
  loop. Nothing narrows permanently and he does not have to know any of this.
- **The note stops saying something untrue.** It read *"This Mac no longer has 192.168.1.21"* — a
  statement of fact from a single reading, and the wrong fact. It now says the Mac does not have the
  address **yet**, that this is usual for a minute after a restart, and that the services will restart
  by themselves when it appears.

`ShellConfiguration.arrivedAddresses` is the decision, extracted pure with injectable addresses so the
case that matters — a headless boot where the address turns up a minute later — is tested without
waiting for a network. Five assertions in `tests/native/main.swift`, and one of them was **proved to
fail** with the comparison stubbed out.

`native/build.sh` green. **Gate: 1512 tests, OK (11 skipped).**

## 0.6.108 — 2026-09-29 · A node that had joined came up saying it had not



### A Mac that joined no longer says it hasn't

This is the one you spotted on the other computer: **"not joined a controller" at the top while the
bottom of the same screen said you had joined as a node.**

Both lines were reading the same fact from two different places. The bottom line reads your Mac's
**credential**, which was correct — you had joined. The line above it reads the **controller's
address**, which came back empty, so it said you had not. As a result the dot went grey and the
Connections page had no address to check, which is why looking again showed nothing about your
controller even though the connection was real.

**Your Mac was genuinely joined the whole time.** The re-join was working; only the part that tells
the screen was missing, and it was missing on every restart — so a node that had joined would look
detached until you joined again by hand.

Now the address is restored to the screen along with the rest of the membership, so the badge, the
line, the dot and the Connections page all say the same thing. If your controller is off you will
see that it is off, which is different from not being joined — and it will say which.

### Technical notes

**The owner, on a second Mac in the group:** *"i was just connecting and adding nodes but it doesn't
show properly. like i already connected but when i go to check with a fresh, it still doesn't show.
and also sometime it shows not joined a controller. but at the bottom it shows i already joined as a
node."*

**Two lines on one screen, contradicting each other, and both were reading a different property for
the same fact.** The standing line reads the *credential* — `fleetCredentialOrigin == "adopted"` —
and correctly said "Joined to a controller". The health note reads the *address*, found it empty, and
said "Not joined to a controller." The dot went grey, and the Connections page had no address to
check, which is why a fresh look showed nothing about the controller at all.

**The omission is one line, on the startup restore path.** `adoptStartupMembership` copied the saved
address into `storedMembership` — which is what the re-join loop reads, and the re-join was working
perfectly the whole time — but never published it to `controllerAddress`, the property the screen
reads. The app saves the address on join (`onControllerJoined` → `configuration.controllerAddress`,
persisted in `ShellCore`), loads it at launch, and passes it in; only the last step was missing.

**This is the second time this same function has hidden a state the UI needed.** Its own comment
already records the first: the launch called `rejoinControllerIfNeeded()` before anything had fetched
the identity, so the guard saw nil and every Mac returned silently. Same function, same shape — a
restore that moved state into the machinery and forgot the screen — which is why the fix publishes
the address and the test asserts the two agree.

**The test that proves it had to avoid the obvious trap.** The first version asserted the published
address after a restore that *also* joined successfully, and a successful join publishes the address
from the join target — the same string — so the defect was invisible behind it. It passed with the
fix removed. The assertion now runs on a restore that produces **no join** (the identity read fails
first, which is the realistic case on a node that boots before its controller), and uses a
**different** address from the previous block, so a merely stale value fails too. With the fix
reverted it fails with `got: 192.168.1.21:8080` — the previous address, which is precisely what a
node was showing: a membership that was fine, behind a screen that still said otherwise.

`native/build.sh` green. **Gate: 1512 tests, OK (11 skipped).**

## 0.6.107 — 2026-09-29 · The long-form joiner levels, and Breeze TTS 2 4-bit ships



### Breeze TTS 2 is here — and it's the one you picked

You asked for it shipped, so it is. **Breeze TTS 2 4-bit (MLX)** is now a voice model in the Audio
page. It designs a voice from a description you write, clones one from a saved voice, takes a
direction for how a line should be read, and performs things like `(sigh)` and `(laugh)` instead of
reading them out. 24 kHz, English and Chinese.

It joins long scripts at **0.60 s** — the pause you chose by ear — and it is the reason the next
item got fixed.

**One thing to know about cloning with it:** Breeze needs the reference clip **and its exact
transcript**. If you pick a saved voice that has no transcript, it will say so and point you at the
Voices page rather than quietly producing a voice that doesn't match. That's deliberate — a guessed
transcript would make the clone wrong, not just worse.

It wants **16 GB**. The memory it uses while running is small, but one measurement of its peak never
settled, so the floor is the honest one rather than the flattering one.

### Long scripts no longer step up and down in volume

This is a real fix to every voice model, not just Breeze.

A long script is made in sections, one after another, and nothing was telling each section what
level the one before it came out at. On your own 1,200-word Aiden render the sections varied by
**4.9 LU** — audible as the narrator stepping back from the microphone at each paragraph and forward
again at the next.

My first attempt was the obvious one, and it barely helped: 4.9 down to 4.3. The quiet sections were
exactly the ones with tall peaks, so the ceiling stopped them being lifted. The pass that works
brings the same sections to **1.5 LU**, and produces a file measuring **exactly what the sample you
approved measured**. That's what's in now.

It only changes level — the length is untouched — and if the meter can't run for any reason the
script is still joined as before, with a note saying so.

### The voice engine underneath was upgraded, carefully

Breeze only exists in a newer version of the speech engine, so that engine moved forward. Before it
did, it was checked two ways: every call the app makes is unchanged, and the one voice model whose
behaviour genuinely changed was rendered side by side, old against new — **16 out of 16 runs
finished cleanly and passed the quality check on both.** The difference in output is real but small,
and it's smallest on the long-script path the app actually uses.

Two things I could not check and am not going to pretend otherwise: the **larger Qwen clone model
wasn't measured** (its files aren't on this Mac), and the rare failure this change touches didn't
reproduce in either version, so it's bounded rather than settled. Both are written down in the
project record.

### Technical notes

Two things the owner asked for in one release, because Breeze is the reason the first one finally
got measured.

### The joiner now matches section loudness — and the obvious fix was the wrong one

**The owner asked directly:** *"did you fix the loudness and the make the joiner work?"* He was right
that it did not. `_join_long_form_wavs` had **no loudness matching for any family**, so a long script
came out at whatever level each independently-rendered section happened to land on. Measured on
Breeze TTS 2 4-bit: the 14 sections of one 1,227-word script span **4.9 LU** (−30.0 to −25.0 LUFS),
which the ear hears as the narrator stepping back from the microphone at every paragraph.

**The first implementation was one linear gain per section, and it was measured to fail.** The
sections span 4.9 LU of loudness *and* **18.4 to 22.7 dB of crest factor**, so what actually decides
each section's final level is how much gain it can take before its peak reaches the ceiling.
`min(target − I, ceiling − TP)` narrowed the span only **4.9 LU → 4.3 LU** — every quiet section was
capped, and the step survived the fix. FFmpeg's EBU R128 `loudnorm` per section brought the same 14
sections to **1.5 LU**, and the joined file measured **−17.50 LUFS / −1.42 dBTP — identical to the
sample the owner listened to and approved.** That is what ships, and the rejected measurement is
recorded beside the constants so the next person does not re-derive it.

Each family declares `section_loudness_target_lufs` on its `catalog.FAMILIES` entry (`None` opts
out), with the true-peak ceiling and loudness range beside it. The pass is length-preserving —
measured ratio **1.000** at 50 ms, 200 ms and 1 s — so it changes level and nothing else; where
FFmpeg is missing or a section cannot be levelled, the join proceeds unlevelled and **says so**,
because a job that has already spent twenty minutes generating must never be lost to a meter.

### Breeze TTS 2 4-bit (MLX) is a family, a model, and a manifest

`mlx-community/Breeze-TTS-2-mlx-4bit` at revision `3a06d26b` — 12 files / 3,042,440,328 bytes — with
a `design_clone_or_direct` resolver, an entry in `MLX_AUDIO_FAMILIES`, and the **0.60 s join pause
the owner chose by ear** from three cuts of the same Aiden render. It designs a voice from a written
description, clones one from a reference clip plus its transcript, takes a direction for how a line
is read, and performs inline vocal events such as `(sigh)` rather than reading them aloud. 24 kHz
mono, English and Chinese, **16 GB floor** — 16 rather than the 8 GB the resident size would allow,
because RSS held flat at ~3.3 GB while the kernel's peak-footprint reading ranged 5.41–23.93 GB and
was never settled. 16 GB is also where 50 of the owner's ~53 machines sit.

**Breeze refuses a voice with no transcript rather than faking one.** It builds its clone prompt from
`f"{speaker}{ref_text}"`, so the placeholder Pocket TTS's resolver substitutes would not degrade the
clone, it would condition it on the wrong words. The refusal names the Voices page.

### The runtime moved 0.4.7 → 0.5.7, and it was measured first

`breeze_tts` exists only in mlx-audio 0.5.7, so the shipping voice runtime had to move. A runtime
swap is exactly the shape that shipped an unnoticed mflux change for five releases, so it was
audited and then **rendered**: every entry point the worker calls is byte-identical
(`generate_audio`, `load_model`, `GenerationResult`, all seven model `generate()` signatures), no
dependency floor rises, **769 voice tests pass identically**, and a 16-render A/B on the one family
with a real behavioural change found **16/16 terminating by EOS and 16/16 accepted** by the project's
own Whisper quality gate.

The change is real and stays recorded: Qwen3-TTS's clone sampler now applies its forced 1.5
repetition penalty to the **last 64 tokens** instead of the whole history, and no longer restores the
EOS logit after top-k/top-p filtering — confirmed live at runtime, and the audio is genuinely
different (same-seed cross-version waveform correlation ≈ 0). What is the same is everything the
project measures, and the **shipped sectioned path is where the delta is smallest** (same-seed totals
within 1–3 tokens). **Two residual risks, stated rather than buried:** the **1.7B Base was not
measured** — no real copy exists on this disk, only dangling symlinks onto an unmounted drive — and
the rare EOS-failure tail did not reproduce in either version, so it is bounded (≤1 in 5 single-shot,
≤1 in 3 sectioned per arm at 95%) without being resolved. The pin is one line either way:
`2c9461f5…` **is** v0.4.7, `94c77162…` is v0.5.7, and the regenerated lock changed **nothing else**.

`tests/test_breeze_family.py` (11 tests) pins the three forms, the transcript refusal, and the
registry wiring. `tests/test_long_form_loudness.py` (12 tests) drives the real joiner over real WAV
files and measures the result — the level test was **proved to fail** with levelling disabled, and
the empty-file test reproduces the `30 != 10` failure that caught a section being silently emptied.
`docs/model-benchmarks.md`, the manifest inventory and five package counts were regenerated for the
31st package. **Gate: 1512 tests, OK (11 skipped).** `native/build.sh` green.

## 0.6.106 — 2026-09-29 · A clip that will not play now says why, and names the Mac that has it

### A clip that will not play now tells you why, and which Mac has it

You asked what happens when a finished clip will not play, and said "502" is not an answer. You are
right, and it is not the answer you get any more.

The reason is not a fault in the clip. A finished result is kept **on the Mac that made it**, not
inside Studio Hub — the Hub only fetches it from that Mac when you press play. So if that Mac is off,
asleep or disconnected, there is nothing to fetch. Studio Hub now says exactly that, and names the
machine:

> *"This result was made on studio-2 and is stored on that Mac's disk rather than in the Hub, so it
> can only be played while studio-2 is running and connected — and it is not running or not connected
> to this Hub right now. Start that Mac, or wake it, and play this again."*

If the Mac is on but its engine is not answering, it says that instead, because the thing to try is
different. And on a single-Mac setup it points at this app's own services rather than at another
computer.

We did **not** change where results are kept, per your answer — no copies, just a clear reason.

### Technical notes

**The owner, asked whether the Hub should keep its own copy of every finished artifact so a result
stays readable when its worker goes offline:**

> *"No not at all. no need to copy. just give a proper clear reason why it can't be played. 502 is no
> proper reason. it must be understandable from a glance. so i know why it didn't play."*

So the behaviour is unchanged and the **explanation** is the fix. That is the right split: copying
every output to the controller would put a second full copy of every render on every machine in the
fleet, against a written decision (`backend/job_storage.py`: *"Image and voice generation artifacts
live on the worker Macs"*), to avoid a sentence.

**What he was reading was `Local engine HTTP 502: worker artifact could not be read`.** Two things
were wrong with it and the status code was the smaller one. "502" names the layer that failed rather
than the cause; and "worker artifact" names a component he has never seen — there is no worker
artifact anywhere in what he uses, there is a clip and there is the Mac that made it. Only the Hub
knows which Mac holds a result, so the Hub's own `detail` is now the message and it says the three
things a person needs: **what** happened, **which Mac**, and **what fixes it**.

`standalone.artifact_unavailable_reason` tells the two causes apart, because their fixes differ:

- **A worker missing from the roster** is a Mac that is switched off or disconnected —
  *"This result was made on studio-2 and is stored on that Mac's disk rather than in the Hub, so it
  can only be played while studio-2 is running and connected — and it is not running or not
  connected to this Hub right now. Start that Mac, or wake it, and play this again."*
- **A worker still registered that does not answer** is one that is running and unhealthy, and the
  same sentence says "not answering this Hub" instead.

A single-Mac install has no machine name and gets the local form, pointing at this app's own
services. The third refusal on that route — a stored reference that does not match the worker's
origin — is deliberately **left as an internal error**: it is a Hub-side integrity failure the
operator neither caused nor can act on, and dressing it in "start that Mac" would send them to
restart a machine that is running perfectly well.

**The app half.** `validateResponse` used to prefix every refusal with `Local engine HTTP N:`. It now
shows the Hub's sentence first and appends the code — `"…play this again. (HTTP 503)"` — because the
code is what makes a report diagnosable and a sentence the operator reads first costs nothing.
Removing it entirely broke `a real failure must carry the engine's own answer`, which was right to
complain: the fix was the order, not the absence.

`tests/test_artifact_failure_reason.py` (11 tests) pins both halves: the sentence names the Mac, the
machine falls back to the `role@machine` studio id when the item does not state one, the reason
carries no status code and is a sentence, all three refusal points on the route are as described,
and the app shows the Hub's `detail` with the code after it. `native/build.sh` green.

## 0.6.105 — 2026-09-29 · The reveal button refused without saying why, and macOS never showed the tooltip

### Show in Folder now tells you why when it can't

The button works now — but it was still going grey and saying nothing about it. Its explanation was
in a tooltip, and macOS doesn't show a tooltip on a greyed-out button, so there was nowhere on
screen to read the reason.

It is always clickable now. If the file is on this Mac, Finder opens with it selected. If it isn't,
Studio Hub says so at the top of the page, and it tells you which of two things happened:

- **The clip was made on another Mac** — so there is no file for it here. It names the Mac that has it.
- **The file has been moved or cleaned up** — it says the file is no longer where it was recorded.

Before, both of those got the same sentence, and that sentence was wrong for the second one.

### Technical notes

**0.6.104 made Show in Folder work. This makes it explain itself.** Both halves were the same report
— *"even on this machine, i can't click show in finder too"* — and only the first was a bug in the
data.

`revealButton` was `.disabled(!job.canRevealArtifact)` with its explanation in a `.help` tooltip:
*"This result is not a file on this Mac, so there is no folder to show."* **macOS does not show a
tooltip for a disabled control.** So the button declined in exactly the one situation where it had
something to say, and said it nowhere — a grey control with no explanation on screen, which is the
state this project keeps having to fix.

The button is always clickable now. Where the file is here, Finder selects it. Where it is not, the
row reports why through the workspace banner, which is where every other file action in the app
reports — and it says which of two different things went wrong:

- **No path published** — the Hub only records a path for a job this Mac produced, so this means the
  job ran on another Mac. The message names that and points at the Mac that has the file.
- **A path with no file behind it** — the file has been moved, renamed or cleaned up since. This is
  a different fact, and the old sentence got it wrong: it called a local file that had been deleted
  "not a file on this Mac".

Also new `NativeJob.revealUnavailableReason`, and four tests in `tests/test_local_artifact_reveal.py`
(17 in the file now) pinning that the button carries no `.disabled`, that a refused reveal reaches
`fileActionError` rather than a tooltip, and that the two causes stay distinguishable. The two
behavioural ones were proved to fail with the old disabled button restored.

`native/build.sh` green.

## 0.6.104 — 2026-09-29 · The four things the job rows were missing

### Show in Folder works now — it never could

The button was there on every finished row and it was dead on every finished row, on every Mac,
including the one that made the file. The Hub was storing where each result went and then removing
that field before the app ever saw it, so the button had nothing to point at and stayed grey.

A result this Mac produced now carries its own location, and **Show in Folder** selects that file in
Finder. A job that ran on another Mac still shows a disabled button with the reason in its tooltip,
because there is genuinely no folder on this Mac to open.

### Every row says when it was generated

The rows told you what made a clip and how long it took, but never *when* — so two runs of the same
script looked identical and the only way to date one was to count down from the top. Each row now
carries the date and time the job was filed, in the same style the rest of the app uses. A job the
Hub never stamped shows no date rather than a wrong one.

### The rows are separated, and the player no longer jumps the list

Two things about the same panel. The rows sat directly on the workspace background with nothing
between them, so a long history read as one block; there is a divider between rows now, the same as
the Jobs page has always drawn.

And the player bar above the list used to appear only once you pressed Play on something — which
pushed the whole list down as it appeared and pulled it back up when you stopped, moving the row you
were reading out from under the pointer. The player is now always in place. Before anything is
loaded its controls are greyed out and it says what to do to load a clip.

### Technical notes

**The owner, from screenshots of his own Audio and Jobs pages:** *"the audio scrubber is appearing and
disappearing … it shouldn't move the list"*, *"i think we should have a divider or a line between
them — they all in the same black background"*, *"even on this machine, i can't click show in finder
too"*, and *"there's is no generation date so i don't know when each of them was generated on."*

Four reports about one screen. Each was a thing written **once per panel or once per row builder**,
and therefore missing or wrong in five or six places at the same time.

**1. Show in Folder was disabled on every row of every machine.** `broker.public_item` — the function
every job payload is built by — dropped `artifact_path` from every item it published. The app's
`canRevealArtifact` reads `item.artifactPath` and checks the file is there, so the field arrived nil,
the check was never reached, and the button was disabled everywhere, **including the Mac that wrote
the file**. The app's own docstring already described the intended behaviour — *"The Hub records the
worker's `output_path` on the item and this reveals exactly that"* — so it was the publication, not
the button, that was wrong.

The rule the strip came from is real and is kept: a path recorded by a worker on **another Mac** names
a file on that Mac's disk, and offering a reveal Finder cannot perform is worse than a disabled
button. `local_artifact_path` publishes the path only when `peers.is_local_studio` says the producing
worker is this Mac — the same test the broker uses to decide whose memory a worker's is. The app's
`FileManager.fileExists` check is the second gate and still covers a remote job's path.
`worker_artifact_url` and `worker_accepted_params` are still withheld from every item.

**2. The rows had no boundary.** Each panel drew `ScrollView { LazyVStack(spacing: 12) { ForEach } }`
straight onto the workspace background, so there was nothing between one row and the next. The Jobs
page had drawn a `Divider()` all along. All five panels now render through one `RecentRows`, which
draws the row and then the divider, so no panel can be the one without it.

**3. The transport appeared and disappeared.** It was drawn only while a clip was loaded, so pressing
Play pushed the whole list down and Stop pulled it back — the row being read moved under the pointer.
It is now always on screen, takes an optional job, and is inert with a one-line reason until
something is loaded.

**4. No row carried a generation date.** Six row builders each hand-wrote the same "status · model"
`HStack`, and the date was written into none of them. That line is now `jobMetaLine`, in one place, and
it renders `NativeJob.generatedAtLabel` — the Hub's own `batch.created_at` through the same
`moment(_:)` formatter the fleet pages use. A job with no stamp shows no date rather than 1970.

`native/build.sh` green (three suites). `tests/test_recent_job_lists.py` grew from 3 tests to 14 and
now pins the divider, the transport and the date **by shape** — one `RecentRows`, one `stateBadge`
call site, one meta line — so the sixth panel and the seventh row builder are caught. New
`tests/test_local_artifact_reveal.py` (14 tests) pins both halves of the path contract: a local job
publishes its path, a remote job does not, and the app still refuses to reveal a file that is not
here. `tests/native/api_main.swift` adds four checks for the date and the reveal gate. Each fix was
proved to fail with the defect restored.

## 0.6.103 — 2026-09-29 · Every studio's "Recent" list was capped at five, so it could not scroll

### Every studio's "Recent" list now scrolls to everything

The small list beside each generator — Recent images, Recent audio, and the same on Music, Video and
Subtitle — stopped at five items and would not move. It looked like it was refusing to scroll; in
fact it was only ever given five things to show, so there was nothing below to reach. Every one of
those panels now lists your whole history and scrolls normally.

### Technical notes

**The owner, with a screenshot:** *"audio generation tab only shows a few and i can't scroll it about.
like there's more stuff but it's stuck there. and i think the same goes to the other generators as
well."*

Both halves were right. Each studio page wrapped its list in a `ScrollView` and then handed that
scroll view `.prefix(5)` — so five rows were all it ever had. **A list capped at five cannot be
scrolled by definition**, and the panel read as *refusing* to move rather than as having nothing more
to show. The same five-line mistake was in **Image, Music, Video, Audio and Subtitle**; the cap was
copied along with the layout, which is why he saw it on more than one page.

Nothing explained the 5. It reads as "recent", but a recent list is one the reader can scroll past —
this was a ceiling, and it hid his own history behind a scroll view that had nothing to scroll.

Five caps removed. `tests/test_recent_job_lists.py` pins it by shape rather than by count: no studio
list may pass a fixed slice of its jobs, each must still render its list (the other way to pass is to
stop showing it), and there must be at least as many scroll views as lists. Proved to fail with the
cap restored.

## 0.6.102 — 2026-09-28 · One token to copy, and the screen says which

### There is one token to give out, and the screen now says which

You asked to merge the two tokens. The part that was actually costing you time was not the two
values — it was that **nothing told you which one to use**. Connections showed both the same way,
each with its own copy button, and the Join box only said *"not its Hub token"*, which names the one
to avoid instead of the one to take.

Now Connections leads with **“Give this out”** and the Fleet token — that is the one for GatewayKH and
for every Mac that joins — and the Hub token sits below a line under **“Keep this one.”** The Join box
says to copy the token marked *“Give this out”*, and nothing else to choose between.

**Why I did not make them the same secret**, since you should know what you decided: the token you
hand out deliberately cannot administer the Mac you handed it to. If it were the same as your private
key, then GatewayKH and all fifty nodes would each hold a key that can change any Mac's settings,
remove its models and throw machines out of the group. And removing a Mac you have sold would stop
being one click — it would mean changing the token on every Mac you kept, which needs them all
switched on.

Nothing about the tokens themselves changed, so **GatewayKH needs no update** and no Mac needs
re-joining for this.

### Technical notes

**The owner asked to merge the two tokens into one: *"can we just make it use only 1 token? do we really need both?"* — then chose the version that keeps the safety property: *"one token to copy — merge the story, keep the secrets."***

**Why the values stay separate, in the code's own words.** `peers.fleet_token` already said it:

> *"A packaged Hub owns a fleet credential of its own, **distinct from its Hub token. The separation is the point** … it is also the whole reason a node's owner cannot administer the controller that owns them."*

And the Connections page already told the operator: *"A Mac that holds this token still cannot administer this Mac."* Merging the **secrets** would end that — the Gateway and every one of 50 nodes would hold a credential that can change any Mac's settings, install or remove its models, delete its output and evict machines. The sharper cost is operational: revoking one sold Mac is one click today, because its own credential dies with its row; with a shared token it becomes a fleet-wide rotation that needs every Mac reachable.

**So the *story* is merged instead, which is what actually cost him time.** The screen drew the two as peers — two rows, two copy buttons, no statement of which to use — and the join form's only instruction named the thing **not** to use (*"not its Hub token"*). He read a token off the joining Mac, pasted it, and the controller refused it with a message that made his token look wrong.

- **The token to hand out is first, and it says so.** The Connections page leads with **“Give this out”** and the Fleet token, then the Hub token below a rule under **“Keep this one.”** A single sentence carries the instruction: *"This is the only token you hand out."*
- **The join form names what to use, not what to avoid.** *"…its Fleet token — the one that Mac marks “Give this out”. On the controller, open Connections and copy it from there."* The `— not its Hub token` clause is gone.
- The old closing line (*"the Hub token is this Mac's key, the Fleet token is the work badge for the group"*) is removed rather than reworded: it existed to explain a choice the operator should never have been asked to make.

**No value changed, and the Gateway needs nothing.** This is the two tokens explained properly, not merged — so `gateway-kh` keeps using the Fleet token exactly as it does, and no Mac has to be re-enrolled for it. The security property in `tests/test_peer_path_contract.py::FleetCredentialWiringTests` is untouched and still asserted.

Three tests pin the framing so it cannot drift back: the token to hand out comes **first**, the screen says **“Give this out”**, and the join form no longer contains `not its Hub token`. Proved to fail on the old wording.

## 0.6.101 — 2026-09-28 · The Join button never sent the token you typed

### Joining a controller now uses the token you type

When you joined a Mac to a controller and typed the controller's Fleet token, the app **sent an old
token it was holding instead**, and never sent yours. The controller refused it, correctly, and the
message blamed the token you had just pasted — which was right. That is why neither the Hub token nor
the Fleet token appeared to work: neither was ever sent.

It happened on any Mac that had ever been part of a group before, which is every Mac you are
re-joining. The Join button now sends what is in the box. A Mac that already holds its **own** node
credential still presents that first — it is the one value that survives rotating the group token —
so re-joining after a rotation keeps working exactly as it did.

If you were blocked joining a controller, this is the fix. It needs to be on both Macs: update the
controller and the node.

### Technical notes

**Found in the field, on the owner's first real node.** A healthy node (0.6.100, six workers up) joining a healthy controller, with that controller's own Fleet token pasted into the box, refused every single time:

> *Join the controller failed. … a credential that is neither this Mac's Hub token nor the Fleet token this Mac holds. … If the caller presented a Fleet token, the two Macs hold different Fleet tokens.*

Both tokens were correct, and both worked when used directly against the controller — a real join with each returned **201**. **The app was not sending the one he typed.**

`joinController` chose its first credential with

```swift
let own = nodeOriginCredential ?? secret
```

and `nodeOriginCredential` returns what this Mac **already holds** — its own node credential first, else the group token it stored — falling back to what the operator typed only if it holds nothing at all. That order is right for the **startup re-join loop**, where the Mac is re-announcing itself and nobody typed anything. It is wrong for the **Join button**, whose own screen says *"Enter the controller's address and its Fleet token"*.

And the retry that should have rescued it could not: it was gated on `heldNodeCredential`, which a Mac holding a stored **group** token and **no** per-node credential does not have — the state of every row written before per-node credentials existed, which is every Mac the owner is re-joining. So the one control an operator has for correcting a membership did nothing, and the page blamed his token for a request that never carried it.

- **A Mac's own node credential still goes first, always.** It names this Mac alone, so it is the value that survives a group-wide Fleet-token rotation, and a re-join presenting it is correct whether a person pressed Join or the loop is running. An existing assertion pins that — and it caught the first version of this fix, which had the operator's token outranking it.
- **A stored group token has no such privilege.** It is only the last group credential this Mac was given, and it goes stale the moment it joins a different controller. So when the operator is the one asking (`startupAttemptID == nil`; the loop always passes one), what he typed goes first.
- **The retry presents the *other* credential, once.** The first version computed it as `heldNodeCredential ?? secret`, which offers the same value a second time when the held one is what failed — the re-admitted-Mac case, where the group token is the one that gets it back in. A second pre-existing assertion caught that.

**Proved by putting the old line back**: the native suite fails with the wire carrying `token-from-an-earlier-group` — the stale value — where the typed token should be, and passes with the fix. Both halves are pinned in `tests/native/api_main.swift`, alongside the assertions that predate them.

**What this means for the rollout:** this is the control every node uses to join, and it was unusable for any Mac that had ever been in a group. It has to reach the drives before the fleet can be enrolled.

## 0.6.100 — 2026-09-28 · The recorded-open list had three items on it that were already done, and the video harness would have refused on the Mac it exists for

### The video test would have failed on your 16 GB Mac for the wrong reason

The test looks for the model in your home folder as well as on the drive, and it was looking under
the **wrong name**. It knew the older spelling — the one with dots, from a manual download — and not
the name the app actually installs. So on a 16 GB Mac where step 2 had just put the model in place
correctly, the test would have said *"the model is not on this Mac"* and stopped. It now knows both
names, and the app's own spelling is checked first.

### Installing Studio Hub left it switched off

The install step quits Studio Hub so it can replace it — and then never started it again. This was
harmless-looking and wasn't: until you opened it, your Gateway showed every Studio Hub location
unreachable and every model list on every Mac came up empty. It starts the app again now, and waits
until the Hub is genuinely answering before saying it is done.

### The chat lane streams, measured

Chat replies now have a recorded end-to-end streaming run through the Hub, not just an assumption:
real word-by-word output, a proper finish, token counts. **Nobody has typed into the chat page
itself yet** — that is still yours to try, and it is the last thing on the chat list.

### Cleanup worth knowing about

Two SSD copies of the LTX video model were **cut short** (a 1.9 GB file where it should be 9.9 GB) in
each drive's plain model archive. Both now hold the complete file, copied from the checked copy on
the same drive. A stale half-downloaded duplicate in your own home folder was moved to
`~/StudioHubModels/_trash/` rather than deleted.

### Technical notes

Finishing what rounds 18–20 recorded. Three things, and the first is the one that would have cost
the owner a trip.

**1. `RUN-16GB-LTX-VIDEO.command` would have refused on a 16 GB node — the machine it exists for.**

Its home-folder candidate list named **only the legacy spelling**: `~/StudioHubModels/ltx-2.3-22b-ram-12gb-mlx`
(upstream's dots, flat, no revision folder). The app, the kit's copy step and the worker all use the
**package id** instead — `ltx-2-3-22b-ram-12gb-mlx`, installed as `<library>/<package_id>/<revision>/model`.
So on a node where step 2 had just installed the model *correctly*, the script found nothing and
would have stopped with *"the model is not on this Mac"*.

Measured on this Mac, both spellings present: the package-id copy is **18,674,268 KiB and complete**,
the legacy dot-form is **10,760,852 KiB and partial**. Both are listed now, package-id first, in both
the revision-folder and flat shapes. **Proved with the drive removed from the candidate list** — the
exact node case: the old list answers *"Every copy of the model on this Mac is unfinished"* while the
new one resolves `…/ltx-2-3-22b-ram-12gb-mlx/d8a9d2f…/model (18G)` and runs.

**2. `install_local.sh` stopped Studio Hub and never started it again.**

Measured after the 0.6.97 install: the Hub was down and nothing answered on 47873 until it was
opened by hand. A release that ends there looks finished and is not — the Gateway reports every
Studio Hub location unreachable, every model picker on the fleet empties, and each drive's installer
talks to a machine that is not there. It now relaunches and **waits for `hub-ready.json`**, which the
Hub service writes once it is actually serving, so it cannot report success for a process that is
merely running. Guarded on the real install target like the stop half, so a temp-path install still
starts nothing; a Hub that will not come up says so rather than exiting 0 in silence.

**3. The install guards were collected by nothing.** `tests/test_install_local.py` was written
pytest-style — the only such file in `tests/` — while the release gate is
`python3 -m unittest discover`. Measured: **the gate collected 0 tests from it**, and its two tests
(which exist because an install once left `/Applications/Studio Hub.app` with no `Info.plist`) passed
only when pytest was run by hand. Ported to `unittest`: **3 tests now, all collected**, including a
new one asserting the relaunch sits inside the real-target guard — proved to fail when it does not.

**And the recorded-open list itself was three-quarters stale.** The appendix below the numbered list
said voice publishes no below-tier warning ("two missing links, not one"), that FLUX.2 Klein 4B MLX
4-bit's `79.34 s` traced to nothing, and that chat's memory tier is unmeasured. All three were
already done: voice's three links exist (`catalog.py:1582`, `StudioAPI.swift:7576`,
`StudioViews.swift:2297`), FLUX was closed on 2026-09-26 as **105.943 s** with a guard, and chat's
tier was closed at 0.6.69 — **which the same file says, forty lines above.** Two lists of the same
thing in one file, disagreeing, and nothing compared them.

**Chat's streaming half is now measured** rather than assumed: `POST /api/hub/chat/completions` with
`stream: true` returns real SSE deltas from `mlx-community/Qwen3.5-9B-4bit`, a terminal chunk with
`finish_reason: "stop"` and usage, and `[DONE]` — 0.514 s of generation. **The acceptance half is
still the owner's** — nobody has typed into the packaged page — and the handoff now says plainly not
to record the lane as accepted on the strength of the HTTP result.

**Data repaired, nothing deleted.** ATLAS's and ORICO's model-archive copy of LTX-2.3 22B had the
same truncated `transformer-distilled.safetensors` (1.86 GB against 9.97 GB); both now carry the
complete file copied from the verified kit library **on the same drive**. And this Mac's legacy
dot-form partial went to `~/StudioHubModels/_trash/` — a 10.26 GiB hand-download the harness and the
worker both had reason to pick up and neither should.

## 0.6.99 — 2026-09-28 · The video harness was on every drive, and our own cleanup took it off

### The video test is now on every SSD, and it was pointing at a broken copy

Two things were wrong with the video test you would run on a 16 GB Mac.

**It was on none of your drives.** It had been put on all four on 27 September, and then our own
drive-cleanup step removed it the next morning — because the list of files a drive is supposed to
carry never learned about it. Every SSD's Technical folder was left with the 8 GB Z-Image test and
the chat-tier test and nothing for video. It is in that list now, so it will be kept and refreshed
like the others instead of quietly cleaned away.

**And the copy it picked was truncated.** The test looked for the model in a second model folder at
the top of each drive — a leftover that nothing checks. On two drives the biggest file in that folder
was cut short at 1.9 GB instead of 9.9 GB, so the test died after 24 seconds saying the model looked
*corrupt*. It is not corrupt: that folder is stale, and the complete model has been sitting in the
drive's proper model library the whole time, checked and reported as 30 of 30 on every drive. The
test now uses that one, and if it ever finds only an unfinished copy it stops and says so instead of
giving you a misleading error.

**What this does not do is answer the 16 GB question.** Every video measurement so far was on the
32 GB Mac Studio, and the video lane has no smaller-machine setting to imitate one with — so a test
run here would measure a pretend limit, not a real Mac. It needs one of your 16 GB machines. It ran
all the way through here on the good copy, so the test itself is known to work; take a drive to a
16 GB Mac and run it.

### Technical notes

**Two defects, found by doing the thing the handoff said had never been done: running
`RUN-16GB-LTX-VIDEO.command` end to end.** It failed, 24 seconds in, with

```
[load_safetensors] Tensor 'transformer.adaln_single.emb.timestep_embedder.linear1.bias'
invalid data offsets (8706572288, 8706580480) exceeding the size of the file.
Perhaps an incomplete download or corrupt file?
```

which reads as **a corrupt model**. The truth was a truncated copy on a drive.

**The script looked in the wrong place, and its own comment says why that is expensive.** It resolved
the model from `/Volumes/KH-STUDIO-*/model-library/video-models/…` — the **drive-root** library, a
stale duplicate that **nothing verifies**. Measured across the four drives, its package count is
**43/35/35/34** while the kit's own verified library is **31 every time**; on ATLAS and ORICO
`transformer-distilled.safetensors` is **1.86 GB where the pack ships 9.97 GB**, and UGREEN has no
copy there at all. The glob expands **alphabetically**, so the script chose **ATLAS** — the truncated
one — and the partial-copy check it does have fired as a *warning after selection* rather than as a
reason not to select it. **The complete copy is on all four drives**, inside the library
`verify-kit.sh` actually checks.

- The kit's own library is now tried **first**, by revision path.
- **An incomplete copy is ineligible, not warned about.** A truncated pack cannot load, so running
  it produces a misleading error and wastes the trip — the script now refuses (exit 3) and names the
  first incomplete copy it rejected. An explicit `STUDIOHUB_LTX_MODEL` is still honoured, loudly,
  because that is an operator naming a copy on purpose.
- **And the harness is on the drives at all now — it had been, and our own cleanup took it off.**
  The six files are dated **Sep 27 01:22** in every drive's `_trash/Technical-retired/`, moved there
  on **Sep 28 03:34–03:53** by the managed-set prune that `prepare-kit.sh` runs on every
  preparation, because they were never added to `MANAGED`. So the drives carried the video test for a
  day, then every `Technical/` folder held the 8 GB Z-Image and Chat-tier commands and nothing for
  video — while the LTX pack sat complete on all four.
  **The mechanism is the finding, not the file.** The prune is right to exist — a folder that only
  grows is a folder nobody can read, and one stale copy of a script is how an operator runs the wrong
  one — but **a file placed on a drive by hand is silently retired by the next preparation**, and
  nothing requires whatever put it there to register it. `RUN-16GB-LTX-VIDEO.command`, its runner,
  `ltx-video-measure.py`, `RUN-LTX-VIDEO-WORKER.command`, `ltx-video-worker-run.py` and the README
  are now in `MANAGED` beside the other harnesses, so they are refreshed rather than pruned.

**What the run proved, on this 32 GB Mac.** With the complete copy selected, the harness goes all the
way through: it resolves the app's own bundled Python 3.11 video runtime with no setup, loads the
transformer's **1,415 mixed-precision layers** (`{2: 29, 3: 225, 4: 348, 5: 631, 6: 125, 8: 57}`),
and generates. The one warning it prints — *"no transformer config … using hardcoded defaults"* — is
present in **every** prior run including both successes, so it is a property of this pack and not a
finding.

**The 16 GB tier is still unmeasured, and this release does not change that.** Every LTX number
remains `measured_on: 32 GiB M2 Max`, and the video lane has **no per-tier memory limit to imitate
one with** (`memory_policy.FIXED = True`; the pipeline is built and dropped per job). A capped run on
a 32 GB Mac would measure the cap, not the machine. **The test needs a 16 GB Mac, and now a drive
carries everything it needs to run there.**

## 0.6.98 — 2026-09-28 · The video lane was written down as unbuilt while it was live, offered and already making files

### Your video lane already works — our notes said it didn't

You asked what was wrong with the Hub's video lane. The answer is nothing, and the paperwork was
wrong. The video worker has been live all along: it is running right now, it offers **LTX-2.3 22B**
for both text-to-video and image-to-video, and your Gateway is already showing it to Story Studio
with nothing blocked. It has made real video files — the last two runs both succeeded.

What our notes described as "no worker, no manifest, no contract" was a status list written many
releases ago and never re-read. It is corrected now, with the measurements.

What is genuinely left is one thing that matters to you: **every video test has been on the 32 GB
Mac Studio.** Nobody has run it on a 16 GB Mac — and that is most of your fleet. The test harness
for it exists but was never put on the SSDs, so there is currently no way to run it from a drive.
That is the next honest step for video, not building the lane.

### Technical notes

**Asked what was wrong with the Hub's video lane, the honest answer was "nothing, and this file
said otherwise."** The handoff's open-items list was headed *"as of 0.6.71"* — **twenty-six releases
stale** — and its video entry read:

> *Nothing about it is shipped: no worker, no manifest, no `lane_contract`. … a 7,200 s deadline …
> correct, and unbacked until the worker exists.*

**Every clause was false, and three of them had been false for a while.** Checked against the
running system rather than against another document:

| The claim | What the live Hub answers |
|---|---|
| no worker | `studio_type: video`, `studio_version: 0.6.97`, `online: true`, `ready: true`, serving `video.generation`. `COMPONENT_WORKERS['video'] = _video_spec` spawns it from the release manifest. |
| no manifest | four ship, all published as `modality: video` |
| no `lane_contract` | LTX publishes operation `video.generation`, **eight** parameters and **two measured runtimes**; Lance publishes seven |
| the deadline is unbacked | 298.5 s (97 frames, 704×448) and 351.628 s (49 frames, 1024×576), each with its receipt, `measured_on: 32 GiB M2 Max` |
| it will not appear as an offer | the Gateway publishes `studio-hub::baa-ai/LTX-2.3-22B-RAM-12GB-MLX`, `available: true`, no blockers, modes `image-to-video` + `text-to-video` |
| never run end to end | 13 jobs in the worker's own store. The 11 failures are **one 35-minute debugging window** on 09-26 22:33–23:08; the two runs after it **both succeeded** (1,639,307 B in 378.0 s, 381,087 B in 110.3 s) and both `.mp4` files are on disk. |

**This is `AGENTS.md`'s own rule, inverted, and the inversion is the lesson.** The rule warns that
*"implemented"* is not *"in the build you are running"*. This was the reverse — **a shipped, live,
offered feature written down as unbuilt** — and it is just as expensive: it sends the next session to
build what already exists, and it told the owner his video lane was missing when it was not.

The list now carries the measurements and the **real** remaining gaps, which are narrower:

- **The 16 GB tier is unmeasured, and it is the tier that matters.** Every LTX number is
  `measured_on: 32 GiB M2 Max`, while ~50 of the owner's ~53 Macs are 16 GB or below.
- **The harness that would settle it is on no drive.** `qualification/RUN-16GB-LTX-VIDEO.command`
  exists here; the kits' `Technical/` folders carry `RUN-8GB-Z-IMAGE.command` and
  `RUN-CHAT-TIER.command` and nothing for video — so the owner cannot run the test from a drive even
  though the LTX pack is on every one of them.
- **Lance is the genuinely unbuilt engine**, and the code says so in its own comment.
- **Two successful runs is thin evidence**, not a reliability claim.

The heading carries the version it was written against, and now says when it was last re-read. **A
status list nobody re-reads is a status list that lies**, and the version in its own heading is the
cheapest way to notice.

## 0.6.97 — 2026-09-28 · The check that keeps the drive's index honest was refusing to run, and saying so in a way that read like success

### The drive's front page now really does check itself

Each SSD has a front page listing which version of each app it carries. That list was supposed to be
read off the installers sitting on the drive, so it could never name something the drive does not
have. It never actually ran — the check was looking in the wrong place, and quietly gave up every
time while the numbers stayed correct by coincidence. They were right today and would have been
wrong on the next release, which is the worst way for a thing to be broken.

The numbers on all four drives are now read from the installers beside them, and if the check ever
cannot read one, it says so plainly instead of leaving a page that merely looks current.

### Technical notes

**Every drive's index page has been stamped by hand-copied numbers for as long as the stamper has
existed.** `packaging/kit_stamp_index.py` was written to end a bug recorded three times — a drive
index naming `0.6.63` beside a `0.6.70` bundle, `0.6.70` beside `0.6.89`, and Story `1.4.219` beside
a `1.4.234` installer. It derives each row from the installer actually sitting on the drive.

It was never run successfully. `prepare-kit.sh` passes the bare volume name it loops over, and the
script did `Path(sys.argv[1])` — so `KH-STUDIO-NOVA` was a **relative** path, the kit index was looked
for under the current directory, and the write refused. On every drive, every time.

**What made it survive is the part worth recording.** The refusal was not hidden: it printed
`no kit index at KH-STUDIO-NOVA/Video Generation Kit/Studio Hub KH/README.md`, four lines into each
drive's 30-line preparation log. That message is a real path missing its `/Volumes`, and it reads
like a path. An operator — or a summary that counts `RESULT: PASS` at the end of the same log — has
nothing to catch. So the rows stayed right for four releases **by luck**: the repository template the
drives were copied from happened to name the current versions, while the mechanism that derives them
from the drive was dead. Only the next release would have shown it, and it would have shown it as the
same stale row this script exists to prevent.

- A name with no path separator is now read as a volume: `KH-STUDIO-NOVA` → `/Volumes/KH-STUDIO-NOVA`.
  An explicit path is left exactly as given.
- **A refusal names an absolute path** and asks whether that was the drive, so the next one of these
  is visible in the log rather than merely plausible in it.
- `prepare-kit.sh` passes `/Volumes/$VOLUME` as well. The script is defensible on its own and the
  caller is still corrected, because the caller is the thing that was wrong.
- Two stranded `if __name__ == "__main__":` guards found and moved while fixing this — one inside
  `stamp()` in the stamper (unreachable after its `return`, so it had never run and could not) and
  one **mid-file** in `tests/test_document_inventory.py`, which meant running that file directly
  silently skipped the entire kit-index suite. The second is the same accident in the same shape as
  the first, and it is how a suite of tests stops being run without anything going red.
- Three tests added, asserting the rule rather than the membership: a bare volume name resolves to a
  volume, an explicit path is untouched, and a refusal prints where it actually looked.

Verified on the drives: **ORICO and UGREEN stamped on this pass** (the fix landed between volumes),
**NOVA and ATLAS re-stamped afterwards**, all four now carrying `Story Studio 1.4.235` and
`GatewayKH 0.2.67` derived from the installers beside them rather than from a template.

## 0.6.96 — 2026-09-28 · A drive that looked deliberately lighter was carrying a file that could not verify

### All four drives now carry the same 30 models

Three of the four drives were missing the Lance video model — or so the paperwork said. In fact all
four had it, and three had an **older copy of one small config file** that failed its integrity
check, taking the video model down with it. That is why those drives reported 28 of 30 models. The
correct file is on every drive now and all four report the same 30, verified on each.

### Technical notes

**The owner asked why NOVA had 30 packages and the other three had 28, and whether they could be made
the same. The recorded answer was wrong.** The handoff said *"Lance is on NOVA only, deliberately …
those manifests say 'not shipped', which is true"*. Both Lance packages were in fact on **all four**
drives — and on ATLAS, ORICO and UGREEN the processor's `model/config.json` was **1301 bytes where
the manifest records 1373**, so `verify_installed` refused it every time, and refused the video pack
with it because that pack names the processor as a companion. NOVA carried the corrected file, dated
2026-09-27; the other three still had the 6 August copy.

**So `28/30` was not a decision. It was a stale file failing verification, written up as a
trade-off** — which is worse than either, because it reads as intentional and stops anyone looking.
The three drives' own indexes simply omitted the package, and the omission was explained rather than
investigated.

All four drives now carry the corrected bytes and the same set: **30 of 30 packages,
`models_not_shipped: []`, `problems: []`, 115.3 GiB, 295 model files each**, each drive's verifier
PASS, and the kit manifest regenerated from each drive. The only remaining difference between the
four is `.DS_Store` (4/10/4/4), which the app ignores.

**Two habits this re-learns.** *Present is not correct*: the first version of the copy script
skipped any folder that already existed, which is precisely how the stale file survived three
preparations — the script now replaces and says so. And *a claim about what a drive carries has to be
read off the drive*: "not shipped" was a note about intent, and it was published as a fact about
bytes.

## 0.6.95 — 2026-09-28 · The drive's instructions now describe the drive

### The drive's instructions now match the drive

The sheet that says which build a drive carries was fixed in 0.6.94 — but the install steps a few
lines below still told you to open the *previous* installer, by name, one that had just been moved
to `_trash`. Every filename in those instructions is read from the installer actually sitting in the
folder now, so the steps and the files cannot disagree.

### "2 — Copy the models" uses the app you installed

It used to run the copy using the copy of Studio Hub **on the drive**, which is slower and stops if
you unplug the drive while it works. It uses the app in your Applications folder now, and falls back
to the drive's copy only if you have not installed it yet.

### Technical notes

**0.6.94 stamped the index and stopped there, and the same defect was waiting four lines below it.**
On the three drives prepared today the version *line* said Story Studio 1.4.235 — and the install
step three lines down still said *"Open `Story Studio KH 1.4.219-arm64.dmg`"*, a file that had just
been moved to `_trash`. The Gateway kit was worse: its `CHECKLIST.md`, `01-gateway-host.md` and
`README.md` all named `gateway-kh-0.2.44.dmg`, and its tracked sources were older still — Story
**1.4.219**, GatewayKH **0.2.2**. Anyone following those steps fails at step one.

`packaging/kit_stamp_index.py` now rewrites **every backticked `*.dmg`** in an app's documents from
the installer that is actually in its folder, plus the document's own `**Version x.y.z.**` sentence.
The rule that identifies "this app's installer" cuts a filename **at its version** rather than at its
first hyphen — the Gateway's is `gateway-kh-0.2.67.dmg` (hyphen inside the name) and Story's is
`Story Studio KH 1.4.235-arm64.dmg` (version mid-name), and only cutting at the version handles both
without knowing which app it is looking at. A document may still mention another app's DMG, or a
checksum file, without being touched.

**And the copy step now runs the app you installed.** Both the launcher and the helper searched for
`Studio Hub.app` **beside themselves first**, so a Mac that had just run step 1 still executed the
interpreter from the kit's bundle on the removable drive and read *its* manifests. That is slower,
breaks if the drive is unplugged mid-copy, and leaves two different answers to "which app is this?"
— the launcher's and the helper's. Both now prefer `/Applications`, then `~/Applications`, then the
kit's copy as the fallback for a Mac with nothing installed; the launcher passes the bundle it chose
to the helper as `STUDIOHUB_APP_ROOT`, so the interpreter and the manifests come from one bundle or
the change fails loudly.

**That last fix exposed a hole in the tests, which is worth recording.** Six of them went red the
moment the order changed, because they built a fixture bundle and the helper had been finding the
owner's **real** `/Applications/Studio Hub.app` all along — reading thirty manifests from this Mac
against a fixture's library. A test coupled to a live install is not a test; they set
`STUDIOHUB_APP_ROOT` now.

## 0.6.94 — 2026-09-28 · The kit index names the versions the drive actually carries

### The sheet on the drive can no longer name the wrong versions

The index document on each drive listed the apps and their versions — and two of those three
numbers were typed in by hand, so a drive could carry Story Studio 1.4.235 and GatewayKH 0.2.67
while its own index said 1.4.234 and 0.2.60. They are read from the installers sitting on the drive
now, and the drive-root copy of that document is refreshed from the kit's copy so the two cannot
disagree. If a folder has no installer to read, nothing is written and the reason is printed —
a half-updated index would be worse than a stale one.

### Technical notes

**Preparing the three drives for this round turned up the same defect a fourth time.** The kit's
index document named Story Studio **1.4.234** and GatewayKH **0.2.60** on drives that carried
**1.4.235** and **0.2.67**; the drive-root copy of the same 130-line document, untouched since
2026-09-26, still said **1.4.219**, **0.2.44** and **0.6.70**. 0.6.90 and 0.6.91 fixed the copying
and derived the Studio Hub line from the bundle — and left the other two rows to be edited by hand,
which is exactly the pattern that fails. `packaging/kit_stamp_index.py` now reads both from the
newest installer **on the drive** and rewrites those rows, and refreshes the drive-root copy from
the kit copy so the two cannot describe different drives. It **refuses to write a half-stamped
index**: a folder with no installer, or a filename with no version in it, leaves the document
exactly as it was and says which folder confused it, because a stamped `unknown` would be a new
wrong answer rather than a fix for an old one. Wired into `prepare-kit.sh`, so every future prepare
does it without anyone remembering.

## 0.6.93 — 2026-09-28 · The cluster follows the promotion, not just the credential

### The Macs really do follow a promoted controller now

Promoting a Mac to controller and handing over moved every Mac's key but left the new controller
not knowing who its members were, so the group came back with a capacity of one machine. Each Mac
now introduces itself to its new controller as soon as it is told where that controller is. The
promoted Mac is also no longer reported as a machine that "could not be reached" — it was the new
controller, and the result now says so.

### Technical notes

**A promotion moved every node's token and left the new controller's roster empty.** The §9
rehearsal measured it: the handover reported `verified: 1`, the node adopted the new address and
token, and the new head answered `node_summary.total: 0` — so the site that had three Macs came
back with a capacity of one. The nodes had followed the credential and nobody had told their new
head who they were. A node now **introduces itself to the controller it has just been moved to**,
from the Hub rather than only from the app, using the address the *old* controller reached it at —
which is the only party that knows it, because which interface a Mac answers on is the app's
decision at join time.

**And the promoted Mac was reported as a machine that could not be reached.** It is still on the
retiring controller's roster, so the handover pushed to it, it correctly rejected a token that is
no longer its group's, and the operator's result said `manual: 1` — naming the *new controller* as
the failure. Its row is now forgotten without an eviction record (it is being promoted, not sold),
it is skipped by the push, and it is named in the answer as `promoted` so a machine missing from
the list is visibly recognised rather than silently absent.

Both were found by running the rehearsal, not by reading the code, and both are why that step is
in the release loop.

## 0.6.92 — 2026-09-28 · A removed Mac stays removed, and a cluster follows its own promotion

### Removing a Mac now means it is gone

When you remove a Mac from a group, it stays removed. It cannot re-join itself, not even after a
restart, and the controller says so by name and date if it ever asks again. That matters because you
remove machines to sell them: every Mac in a group now carries its **own** key rather than one shared
key, so removing one machine takes that machine's key away and leaves everyone else's alone. If you
change your mind, re-admit it from Connections and it can join again. Macs you already have joined
keep working — each one picks up its own key the next time it joins.

### Leaving a group actually works now

*Leave this group* used to fail every single time — the controller refused it — while the app
reported success and told you to go and remove the Mac by hand. It works now, and a Mac can only ever
remove itself.

### Replacing a controller no longer means visiting every Mac

Promote one of the Macs to controller, then use **Hand over to…** on the old controller. Every node
in the group is told where the new controller is, so **nobody has to touch the other Macs**. The old
controller keeps its history, stops taking new work, and shows that it handed over and to whom. If a
Mac was switched off at the time, it is named in the result so you know which one needs the manual
route — and if the old controller is already dead, there is no way to tell the nodes automatically,
which the app says plainly rather than failing quietly.

### The Fleet token can be rotated

The group's Fleet token could never be changed, even though it is shown on screen and held by every
Mac — so a leak could not be undone. **Rotate Fleet token** mints a new one and hands it to every
node, and names any Mac it could not reach.

### You can see who has joined

The Connections page now shows each joined Mac's role, studios, version, when it joined and when it
was **last seen**, and it tells you when a controller is refusing a Mac's credential instead of
showing a green light that means nothing.

### Technical notes

**Removal used to be bookkeeping, and the fleet treated it as one.** Deleting a node's row left the
Mac holding the group's Fleet token and re-joining on its next start, so "removed" meant "removed
until it reboots" — and with one shared token there was nothing to revoke, which is why the owner's
*"i don't want it to rejoin back because i'm either planning to sell the machine or the machine is
broken"* could not be honoured. Every node is now issued **its own credential at join**, stored
hashed on the controller and `0600` on the Mac that holds it, and presented on every call that Mac
makes home. Removal revokes it, records the removal by name and date, and refuses that id for ever
with a sentence naming the date and where to undo it. Re-admitting is a deliberate action on the
controller. A machine that was already joined before this release adopts its credential on its next
join, so the rollout needs no flag day and no node is touched by hand.

**"Leave this group" worked 0% of the time and reported success.** It sends
`DELETE /api/hub/fleet/nodes/<its own id>` with the credential it holds, and the Fleet allowlist
refused every such request with a 403 — so the app told the operator it had left *and* told him the
controller still listed the Mac, which it did. The route now accepts a machine removing **itself**
and nothing else: a shared group token identifies nobody, so a Fleet-token caller is still refused,
while a node's own credential may remove exactly the row it owns. The two tests that pinned the old
403 were updated deliberately, and the native fixture — which answered a stubbed 200, which is why
this survived a green suite — now answers the real status.

**A cluster now follows its own promotion with nobody touching a node.** Promotion used to mint a
new Fleet token and leave every node holding a dead one, so replacing a controller meant typing an
address and a token into each of thirteen Macs. The promoted node mints its **own** group credential
— never the old controller's — and the **old controller**, while it is still running, is the party
that hands over: it pushes `{new_controller_url, new_token}` to every Mac in its roster over the
channel it already has, then retires itself (it keeps its history, stops accepting dispatch, and says
in its own UI that it handed over to `<M>` on `<date>`). Nodes that could not be reached are named by
name, with the honest limit stated rather than smoothed over: if the old controller is already dead
there is no channel to the nodes, and that case is the runbook plus SSH over Tailscale.

**And the Fleet token can be rotated at last.** `peers.sync_fleet_token` existed with no caller and
no route while the token was shown in the clear on every controller's Connections page, held by every
member, so a leak could not be undone. `POST /api/hub/fleet` with this Mac's Hub token mints a new one
and pushes it to every node, verifying each write with the new credential before committing locally,
and listing by name any Mac that needs the manual path.

**The Fleet page can see who has joined.** `last_seen_at` is stamped for every node that answers a
probe (batched into one write, so a page refresh is not fifty-three rewrites), the roster publishes the
machines it has removed, and the joined-nodes rows carry `role`, `studios`, `version`, `joined_at` and
whether the controller holds a credential for them. A node's green light is now an **authenticated**
read, so after a controller swap it says *credential refused* rather than showing fifty-two Macs green
while the controller calls every one unauthorized.

**Two smaller truths, both found the same way.** The Hub published the same Whisper weights twice for
one Mac — the Subtitle studio and the Voice studio's STT surface both serve them — which became 96
phantom machine-model rows in the gateway's fleet view; one row per machine-and-model is published
now, with the folded count published too rather than silently subtracted. And every one of the 30
model manifests now states its **quantisation** with the sentence saying where it came from, because
the owner's standing rule is that a model named without its quantisation cannot be checked against
what he has actually run.

**In the app:** a **node is told it was removed** and stops re-joining instead of retrying for ever; a
node's own id is exposed; role changes, leaving and removal are confirmed; a "Send here" pin is
cleared when its machine leaves; and "Send here" is hidden on a Mac that cannot dispatch to the pool.

## 0.6.91 — 2026-09-28 · The drive index now stamps itself

### The drive's version label writes itself now

The sheet on each drive that says which build you have was wrong three times over — it said 0.6.89
while the app beside it was 0.6.90 — because someone had to remember to edit it. It is now stamped
from the app itself as the drive is prepared, so the two cannot disagree.

### Technical notes

**A hand-maintained answer to "which build is this?" has been wrong three times.** The ledger has it
twice — `0.6.63` printed against a `0.6.70` bundle, `0.6.70` printed against `0.6.89` — and this
release is the third: preparing the drives at 0.6.90 copied `Studio Hub KH.md` faithfully, and the
document still said `0.6.89`, because that is what the repository copy said when it was written an
hour earlier. Editing the number again would have been the fourth.

**`prepare-kit.sh` now stamps the two version lines from the app it has just placed on the drive** —
`CFBundleShortVersionString` and the commit in that bundle's own `release-manifest.json` — so the
document beside the app cannot disagree with it, whatever the repository copy says. The repository
copy is corrected to 0.6.90 as well, because a file in git that names the wrong build is the same
defect one level up.

This is the same medicine `README.md`'s Studio Hub row already takes: it names no version at all and
points at the drive's generated `focused-release-manifest.json`. The rule both now follow is the one
this project keeps relearning — **a copy of a fact needs a check, or it needs to be derived.**

## 0.6.90 — 2026-09-28 · The drive index named versions three releases old

### The kit's own paperwork is now part of the kit

The documents that tell you which version is on a drive were three releases out of date — one of them
said 0.6.58 while the app beside it was newer — because nothing copied them when a drive was prepared.
They are copied now, and the Studio Hub line no longer states a number at all: it points at the kit's
own generated manifest, so it cannot disagree with the app in the same folder.

### The drives carry the whole library now

They had 24 of the 30 models. YuE2 (the new singing model), LTX and its text encoder are on every
drive now, each verified against its published checksums *on the drive itself*, so "Copy the models"
has what the catalogue advertises. Lance went on the drive that already had it — you are not using it,
and 15.6 GB on three more drives would buy nothing.

### Technical notes

**The kit's own READMEs were the last thing anyone updated.** Preparing the drives for distribution
turned up the same drift the ledger has recorded before, in both documents at once: the kit `README.md`
named Story Studio **1.4.219** and GatewayKH **0.2.44** while the kits shipped beside them carried
1.4.234 and 0.2.60, and `Studio Hub KH.md` — the page whose whole job is to tell an operator which
build they have — said **0.6.58 / `eea477e`**, twenty-nine releases behind the bundle in the same
folder. The repository's own copy was worse than the drives': it said GatewayKH **0.2.2**.

**Nothing copied them, which is why they drifted.** `prepare-kit.sh` refreshed the app, the Technical
folder and the kit manifest on every drive and never touched the two index documents, so each drive
kept whatever it was born with. Both are now copied from `packaging/kit-readmes/` on every prepare,
and the Studio Hub row in `README.md` no longer carries a number at all: it points at the drive's own
`focused-release-manifest.json`, which is generated from the bundle on that drive and therefore cannot
disagree with it. The two numbers that remain — Story Studio and GatewayKH — are named, and a prepare
that ships a different build of either is now visibly out of date rather than silently so.

**The kit stopped being a subset of the catalogue.** The drives carried **24 of 30** packages. Every
kit library now has the six the catalogue adds on top — **YuE2 3B and its decoder** (the new singing
engine, on no drive before this), **LTX-2.3 22B**, **gemma-3-12b** (LTX's text encoder) and, where
the drive already held the weights, **Lance 3B and its processor** — in the layout a package verifies
against. Each drive's own `.studiohub/installed.json` records them after a full rehash **on that
drive**: a copy is not an installation until the bytes have been read back, which is why
`release_kit.py` said "not shipped" about packages that were sitting on the disk.

**Where the volume already had the weights, nothing was written twice.** NOVA carried Lance in its
exploration library and LTX on all but UGREEN; the kit layout was built by **hard link** — 15.6 GB
adopted on NOVA with no new bytes. **One deliberate gap remains: Lance is on NOVA only.** It is not
pursued, the owner asked for it to be kept rather than removed, and copying 15.6 GB to three drives
for a model nobody will run is not a good trade; the kit manifests on those drives say it is not
shipped, which is true and is what the app offers as a download.

## 0.6.89 — 2026-09-28 · The worker could not read its own second engine

### YuE2 said "not installed" when it was installed

You asked twice, and you were right both times. The files were there and verified; the app was being
told "missing" by the music worker, which had three problems at once — it checked YuE2's package
against ACE-Step's engine name, it looked for YuE2's decoder without the list that names it, and it
described every kind of failure with the same word, "missing". All three are fixed, and the picker
shows YuE2 as installed now. Nothing about the model changed: only what the app was able to say
about it.

### Technical notes

**The owner installed YuE2 and the app still said "Not installed" — and he asked twice, because the
files were plainly there.** They were: the Hub answered `files_verified` / `eligible` for both
packages, while the picker read `missing` from the *worker's* own catalogue. Three defects were
stacked, each hiding the next.

**1. `MusicCache` verified every manifest against one global engine constant.** `catalog.ENGINE` is
`music-mlx`, ACE-Step's token. YuE2's manifest correctly declares `music-yue2` — it runs in its own
pinned interpreter — so constructing the cache raised *"Music manifest engine does not match the
worker"* before a single file was looked for. Every model now carries its own
`ModelEntry.manifest_engine`, read through `manifest_engine_for`, so the check is the model's own.
`runtime` (the executor inside this worker: `ace-step`, `stable-audio`, `music-yue2`) stays a
separate fact from the manifest's routing token, which is what conflating them cost.

**2. The companion check ran without the catalogue that names companions.** `yue2-3b-mlx` requires
`yue2-vae`; `preflight` takes a `catalog` map to resolve that and the worker passed none, so an
installed, hashed, verified package answered `dependency_missing` for ever.
`model_packages.load_catalog(directory)` is the worker-side reader of a manifest directory (the Hub
keeps its own, which degrades per manifest because it feeds a route), and `MusicCache` now passes it.

**3. The route flattened every failure to `"missing"`.** `cache.state` was
`"cached" if cached else "missing"`, so `manifest_unavailable`, `runtime_unsupported` and
`engine_root_missing` all reached the operator as *"download it"*. It publishes the state the worker
measured, with its reason — a refusal that names its cause and is then renamed by the caller is the
defect, not the refusal.

**And `preflight` refused the engine it ships.** It returned `runtime_unsupported` for any runtime
that was not ACE-Step's or Stable Audio's, so YuE2 could never report as installed even once its
manifest was readable. Both engines that bring their own runtime now pass straight through to the
package answer, which is the only question being asked of them.

**Verified against the real library, not only in tests:** `preflight("vanch007/mlx-Yue2-3B")` now
returns `cached` with its checkpoint root on this Mac. New tests pin the engine token against every
shipped manifest, the companion rule (both packages installed for real from the shipped manifests,
with one-byte stand-ins, because the rule under test is `preflight`'s and a stubbed receipt would
skip it), and that the worker hands its package check the catalogue. One neighbouring test that had
pinned the flattened `"missing"` now pins the named cause it was hiding.

## 0.6.88 — 2026-09-28 · The video verdicts, and six things the app could not show you

### The video decisions, written down

**Lance is not being pursued.** You tried its output and the quality is not good enough, so the 16 GB
test this guide was asking you to run is cancelled. It stays installed and offered — nothing was
removed, and its measurements stay published — it is simply not the small video model we are betting
on.

**The canvas you accepted now has its numbers.** LTX-2.3 at **1024×576 true 16:9 with audio** measured
**16.9 GB** of memory at its peak, against the 10 GB the graphics allocator reports on its own. That
is why it needs a 24 GB Mac, and why a 16 GB Mac is refused before anything loads: the weights sit
outside what the smaller number counts.

### If a video run dies with a Metal error, try it once more

The first attempt at that canvas failed after 13 seconds with a Metal "impacting interactivity"
error. That is macOS's graphics watchdog cutting in — the app was holding the graphics chip while the
screen wanted it — not a fault in the app. The same run completed on the second try.

### Six things you could not see, now on the screen

**The buttons hold still.** Every row's buttons were sized by their own label, so the column shifted
as you scrolled. They are one width now, in one column, on every page.

**"Show in Folder" on every finished result.** The question the app could not answer was *where is
it?* Now every row can open Finder on the actual file. A result made on another Mac says why it
cannot instead of opening an empty window.

**Each row says what it was asked for.** Below the model you now see the settings that made it —
steps, guidance, seed for images; length, steps and whether it is instrumental for music; resolution,
ratio, length and whether audio is on for video; the script's length and speed for audio — and for a
clip, its real canvas, frame count and frame rate.

**Video rows show a frame of the clip**, and Preview plays it right there instead of making you save
it first.

**The Audio page has a proper player now**: play/pause, stop, a bar you can drag, the elapsed and
total time, and the speed the clip was made at. Finished audio rows also show how many characters the
script was.

**YuE2 is installed and verified.** It was not missing from your Mac — it was in the folder you
downloaded it to rather than the layout the app checks, and the SSD kits predate it entirely. Both
YuE2 packages are now verified against their published checksums and ready in the Music picker.

### Technical notes

**Two owner verdicts recorded, and one of them cancels a run.** Lance is **not pursued** — the owner
tried its output and the quality does not hold up — so the 16 GB Lance run the hardware README had
been asking him for is cancelled. **Nothing is removed:** the runner stays pinned and staged, both
packages stay in the catalog, and its measured rows stay published, because a model kept as a known
spare is a different thing from a model being recommended. LTX-2.3 at **1024×576 true 16:9 with
audio** is accepted as *"close to acceptable"*, so that is the canvas the fleet plans around.

**The accepted canvas now carries the statistic that decides its tier.** Measured through the shipped
worker's own job path — job `e9caa8c4…`, image-to-video, 49 frames at 24 fps, 2.04 s of output with
an audio track, 258.0 s — **16.92 GB peak process footprint** (`ri_phys_footprint`, sampled at 1 Hz)
against **10.05 GB** on the MLX Metal allocator. The allocator alone reads as though a 16 GB Mac fits;
the footprint says it does not, which is why the published tier stays **24 GB minimum / 32 GB
recommended** and 16 GB stays refused by name. The run is in the LTX manifest's `memory_evidence`, in
`qualification/model-readings.json` (so the generated record shows it), and in the receipt
`qualification/ltx-video-worker-results-20260928-004554/`; the manifest's frozen digest moved with it,
as it must.

**An environment lesson, written where the next person will look.** The first attempt at this canvas
died at 15 % after 13 s with `[METAL] Command buffer execution failed: Impacting Interactivity` —
macOS's GPU watchdog firing on a command buffer that held the GPU while the display wanted it, not a
defect in the lane — and the identical command completed on retry. `qualification/
README-video-hardware-test.md` records it beside the numbers, and the "what to expect by Mac" table
now says the 16 GB refusal is confirmed by measurement rather than merely by policy.

### The app surface: six things reported after a real session with it

**The buttons stopped moving.** Every row action was sized by its own label, so `Save…` was narrow
and `Check Original Result` was wide, and the column shifted as the list scrolled. All of them now
share one width and every row reserves one fixed action column (`RowActions`, `rowAction()`), so the
same job reads the same on its Studio page and on the Jobs tab. Two labels were shortened to fit the
shared width: `Check Original Result` is `Check Result`, because a control that cannot fit its own
column is a control that will drift again.

**Every result can be found on disk.** No row could answer *where is the file?* — the app could play,
preview and save a result but never point at it. The Hub has always recorded the worker's own
`output_path` as `artifact_path`; the app now decodes it and every finished row carries **Show in
Folder**, which reveals the file in Finder. It is **disabled, with the reason in its tooltip**, for a
job whose worker is on another Mac, because a button that opens an empty Finder window is worse than
one that explains itself.

**Every row says what it was asked for.** A row showed the result (length, size, and for images the
canvas) and nothing about the request. `requestFacts(for:)` now prints the controls per modality —
Image: steps, guidance, seed; Music: length, steps, guidance, shift, seed, instrumental; Video:
resolution, ratio, length, audio on/off, mode, seed; Audio: the script's character count and the pace
— one function, read by every row, so the Studio page and the Jobs tab cannot describe one job two
ways. **The clip's own measured facts** (canvas, frames, frame rate, whether it has an audio track)
were already on the Hub's item for Video and are now decoded, so a finished 1024×576 clip no longer
reads as "2.0 s · 381 KB" with no dimensions.

**Video rows have a preview.** A clip was a filename and a byte count until it was saved and opened
elsewhere. The row now shows a still from the clip — fetched through the Hub like every other
artifact, handed to AVFoundation from a file in the app's own Playback folder, and cached as a
downscaled frame — and **Preview** plays the clip in place. The frame is taken a tenth of a second in,
not at zero, because frame zero of an image-to-video clip is the still that was submitted.

**The Audio page has a transport.** It had Play and nothing else: no scrub bar, no elapsed time, and
no way to see the pace a clip was made at. It now carries the same transport the Music page already
had — play/pause, stop, a scrub bar, `0:07 / 0:30` — plus the **speed** the job was generated at, and
the finished rows state the **script's character count**, which is the unit a Voice budget is spent in
and which history never showed. The Music lyrics field gained the character count its prompt box
already had.

**YuE2 is installed, and the reason it was not is worth recording.** The Music page said
`yue2-3b-mlx has no files in this library`. Two separate causes: the **SSD kits do not carry YuE2 at
all** — they were built at 0.6.70, before the package existed, which is the open kit-refresh item —
and the copy under `~/StudioHubModels/yue2-3b-mlx/` was in the layout it was downloaded in
(`model/`, `vae/` at the top), not the layout a package verifies against
(`<library>/<package_id>/<revision>/…`). Both packages are now in that layout, hard-linked so no byte
is duplicated, verified by full rehash against their manifests, and the Hub reports
`files_verified` / `dispatch_state: eligible` for `yue2-3b-mlx` and its `yue2-vae` companion. The
VAE's two licence files were absent from the download and byte-identical copies already existed in
the model package, so they were linked from there — the same publisher bytes, checked against the
manifest's own hashes.

## 0.6.86 — 2026-09-27 · YuE2 joins the Music lane, and the tier is measured rather than claimed

### A third music model, and the only one that sings a full song

**YuE2 3B** joins ACE-Step 1.5 and Stable Audio 3 in the Music picker. It writes a sung song from
your lyrics and a style, at 48 kHz stereo, up to about six minutes. ACE-Step also sings; Stable
Audio cannot sing at all; this one is the strongest of the three at a vocal over a full arrangement.

### What it costs, measured on this Mac

Three runs, 8-bit: **10.8 GB** of memory for a 24-second song, **11.8 GB** for 36 seconds, **11.9 GB**
for two minutes. The memory barely changes with length — the weights are most of it — which is why
the answer is simple: **a 16 GB Mac is the minimum, 24 GB is comfortable, and an 8 GB Mac cannot run
it at all.** It also takes its time: about three minutes of work for a 36-second song.

### Two things the app will tell you rather than let you discover

**Lyrics are required.** This model sings, and it has no "instrumental" switch — ask for a song
without words and the app says so and points you at the two models that can do it. And **the memory
guard is set from your Mac**: the engine refuses to thrash, and the app tells it how much it can
afford instead of assuming 16 GB.

### The measurement page now lists every model it has numbers for

`docs/model-benchmarks.md` — the one page that answers "what can this Mac run, and what has actually
been run?" — was missing the new music model, and it was missing the smaller video model's
measurements too. Both are on it now, with the run each figure came from named beside it, and every
number in the measured table says its unit.

### Technical notes

**A third music engine, and the first that cannot share an interpreter.** `mlx-Yue` pins
`mlx==0.32.2` while the Voice runtime — and therefore the Music overlay built on it — carries
`mlx==0.31.2`, so YuE2 runs in its own pinned Python 3.12 tree (`packaging/yue2-runtime`, the port
at `9253ed13…`, MLX 0.32.2), staged as `runtimes/music-yue2` and driven by a bridge the worker
spawns. That is the same conclusion the video lane reached with its two engines: the engine a
manifest declares decides the runtime.

**What it is for: a vocal.** YuE2 is the only engine here that writes a *sung* song — ACE-Step 1.5
also sings and Stable Audio 3 cannot — at 48 kHz stereo, up to about six minutes. The lane adds it
beside the other two rather than replacing either.

**The tier is MEASURED, because the model card publishes none.** Three runs on this 32 GB Mac, peak
by sampled `footprint phys_footprint_peak` in the process doing the work:

| Configuration | Audio | Wall | Peak |
| --- | --- | --- | --- |
| 8-bit, mode off, 600 tokens | 24.0 s | 58.6 s | **10.79 GB** |
| 8-bit, mode off, 900 tokens, through this app's bridge | 36.0 s | 103.2 s | **11.81 GB** |
| 8-bit, mode off, 3000 tokens | 120.0 s | 429.8 s | **11.93 GB** |

**The peak barely moves with length, because the weights dominate** — 2.66 GB 8-bit AR + 2.93 GB
bf16 NAR + 0.51 GB VAE — and that is what settles the fleet question the owner asked: **8 GB cannot
run it in any configuration this port offers** (the AR is the only quantizable part; 4-bit would
save about 1 GB against an 11.9 GB peak), **16 GB is the minimum**, and 24 GB is comfortable. The
receipts are in `qualification/yue2-music-results-20260927/`, including the run made through the
app's own bridge.

**Two packages, and the bf16 twin ships on purpose.** `yue2-3b-mlx` (9 files, 9.92 GB) and its
decoder companion `yue2-vae` (7 files, 0.53 GB). The generator's capture deliberately includes
`ar-bf16.safetensors`, the 4.33 GB file the 8-bit AR was quantized from, because the port's
`verify_conversion` refuses a converted tree without it.

**The lane refuses what the engine cannot do, by name.** Lyrics are required — this engine sings and
has no instrumental switch, and the refusal points at the two engines that do. The step count is
bounded by the port's two documented configurations (8 = its fast mode, 32 = its standard), the
guidance scale by its own protocol's 0–20, and the mode by the three the port offers. Seconds are
converted to the port's token budget at the measured ~25 tokens per second.

**One test stopped testing a list.** The music contract test intersected the published parameters
with a hard-coded `{shift, instrumental, lyrics, vocal_language}`; the fourth engine publishes
`mode` and `precision`, so the assertion failed on the new engine's honest parameters while still
passing if a declared one were dropped. It now compares the declared set with the published set in
both directions — the invariant, which holds for any engine.

**Two records were behind the fleet, and tests caught both rather than a reader.** The generated
model record (`docs/model-benchmarks.md`) had nowhere to put a second music engine: its engine→lane
table did not name `music-yue2`, so the package could ship and be silently absent from the page —
exactly the drift that page's own test exists to catch. It learned the engine, and the page gained
the two measured rows that had been living only in `qualification/`: **Lance** (8.11 GB in `relay`,
20.0 GB in `parallel`; 184.9 s / 329.5 s) and **YuE2** (10.79 / 11.81 / 11.93 GB). Every cell in its
measured table now states its unit — the configuration-qualified keys (`peak_mlx_gb_default`,
`generation_seconds_low_ram`) had been quietly dropping them. The second record,
`research/st01-runtime-contracts.json`, refused the new engine until it was told what the engine is;
writing that entry exposed that its `video-lance` reason still said the runner "is not adopted into
this build yet", which has been false since 0.6.82.

## 0.6.85 — 2026-09-27 · The documentation said the small video model could not run

### The instructions said the smaller video model could not run

The smaller video model's runner was adopted a few releases ago — it is pinned, packaged, measured
and offered by the app. Five documents still said it was "not adopted yet", including the sheet that
tells you which model to test on which Mac, which claimed the app offers it nowhere. All five now
say what the app does, with the measured numbers: about **8 GB** of memory in the mode a 16 GB Mac
uses, about **20 GB** on this Mac's faster mode. Nothing about the app's behaviour changed in this
release; it is the paperwork catching up with the app.

### Technical notes

**A claim the code had already outgrown.** Five places still described the `lance-mlx` runner as
"not adopted into this build": the video worker's own module docstring, the capture spec's note
beside the processor package, the LTX runtime's smoke-import explanation, the hardware-test README
a person reads before running Lance on a 16 GB Mac, and a comment beside the manifest digest. The
runner was adopted in 0.6.82 — it is pinned at `f8ecc5a8…`, staged as `runtimes/video-lance`,
measured twice, and published as eligible — so every one of those was a document contradicting the
code that ships beside it.

**Why it mattered rather than being cosmetic.** The hardware-test README told a reader that Lance
"is offered nowhere" and that its 16 GB run "is not something these commands can measure" — the one
document whose whole job is to tell the owner which run to do. Corrected, with the measured numbers
it should have carried: **8.11 GB** peak in `relay` mode (what a 16 GB Mac gets) and **20.0 GB** in
`parallel`, both by sampled `footprint phys_footprint_peak`, and the honest remainder — a 16 GB
machine has still not run it, which is the owner's run to make.

**Also corrected in the same pass:** the LTX runtime's smoke check still maps `video-lance` to
`None`, which is right — that engine lives in its own Python 3.12 tree — but the reason it printed
was the stale one; and a typo (`SDD` for SSD) in the same README.

Nothing about the lane's behaviour changes: this release is the code's own account of itself.

## 0.6.84 — 2026-09-27 · The pickers were empty, and the switch reaches the Video page

### The model lists were coming up empty

A test written for the video switch found something worse: the app could not read your Hub's model
catalogue at all. Two things in a video model's published settings broke it — a list of numbers
where the app expected words (`2 s`, `4 s`, `24 fps`), and a field name the app was looking for in
a spelling its reader never produces. Because the catalogue arrives in one piece, one unreadable
model emptied **every** picker: Image, Audio, Chat and Video alike. Both are fixed, and the whole
list of 33 models now reads.

### The small model's sound control is on the Video page

The video page now shows a **switch for every on/off setting the selected model publishes** — for
the big model, that is Audio. Under it is the model's own sentence about what the switch does: it
makes picture and sound in one pass, so leaving sound off does not make the clip cheaper or
quicker, it just writes a file with no audio track. The smaller model publishes no such switch, so
its page shows none, and a setting you never touched is left to the model to decide.

### Technical notes

**A live defect, found by a test written for something else.** The Video page needed a test for
the audio switch, so the native fixture gained a video model with the lane contract the running
Hub actually publishes — numeric enums, canvases and all. The catalogue stopped decoding, and it
turned out the app on this Mac had been in that state for three releases:

- **`enum` is not always strings.** The video lane publishes `"enum": [2, 4]` for
  `duration_seconds` and `[24]` for `frame_rate`; `NativeLaneParameter` decoded `enum` as
  `[String]`, so the response failed with `typeMismatch … parameters[4].enum[0]`. The lane's
  values are now read as JSON values (`optionValues`) and rendered as text for pickers (`options`),
  so `2` is the integer the lane published and shows as `2`.
- **A snake_case `CodingKey` can never match under this app's key strategy.** The decoder sets
  `.convertFromSnakeCase`, which rewrites the JSON's `aspect_ratio` to `aspectRatio` before
  matching — so `case aspectRatio = "aspect_ratio"` in `NativeLaneCanvas` was unreachable and
  every canvas failed with `keyNotFound: aspect_ratio`.

**Why that emptied every list and not just the video page:** `/api/hub/catalog` is decoded whole,
so one unreadable row fails the entire response. Measured against the running Hub 0.6.83 with the
app's own types: 33 models failed to decode before, and 33 decode now, with the LTX lane's five
canvases, its `["2", "4"]` duration options and its `audio` switch. The fixture keeps a video row
with both shapes, so either regression empties the list and fails the completeness assertion by
name.

**The audio switch is on the Video page now.** Every boolean parameter the selected model
publishes gets a toggle, with the lane's own note under it — for LTX that is the sentence saying
picture and sound are made in one pass, so a silent clip is not cheaper or faster. A lane that
publishes no switch (Lance) gets no control, an untouched switch is left out of the request so the
lane's own default decides, and a model change drops a choice the newly selected model does not
publish rather than sending it on.

**Evidence:** `qualification/native-video-switch-20260927/video-page-with-published-switch.png` —
the real page, rendered offscreen against the running Hub by `native/render-page.sh`, with the
switch, the lane's note and the catalogue counts in the sidebar.

## 0.6.83 — 2026-09-27 · Sound becomes a choice, and 16:9 becomes real

### You can ask for a video with no sound

The video screen now has an **audio** switch, on by default. Untick it and the clip is written with
no sound at all. The switch only appears for models that make sound — the smaller model's
text-to-video is silent, so it does not offer a choice it cannot honour.

**One thing to know before you rely on it: a silent video is not a quicker video.** The model
denoises picture and sound in the same pass, so unticking audio does not make generation shorter —
it skips the audio decode and leaves the track out of the file. The lane's own description says so,
so nobody has to guess.

### A true widescreen size

Until now the two widescreen sizes were 448×256 and 704×448 — close to 16:9, not 16:9. There is now
a real one: **1024×576**, exact 16:9, two seconds, measured here at about 9.4 GB of memory and six
minutes for a clip. It comes out clean.

The smaller model was also tried at 16:9 (512×288). It runs, but the picture falls apart at that
width, so it is not offered — that model stays square.

### Technical notes

**Ask for a video without sound.** LTX denoises picture and sound *together*, so the audio it makes
is not a separate pass you can pay for or skip — but it does not have to be written. The lane now
publishes an `audio` parameter, and the switch is offered **only by a model that has any**
(`model.generates_audio`): LTX shows it, Lance's text-to-video is silent and shows nothing. It
defaults on, and unticking it sets the pipeline's `generate_audio` attribute to `False`, which skips
the audio decode and writes an MP4 with no audio track.

**The honest note, because it is the whole point of the switch:** a silent run **is not a faster
run**. The joint denoise is unchanged; only the decode and the track are skipped. The contract says
so in as many words rather than letting the checkbox imply a saving it does not make. The artifact
gate matches the request: a track is required only when `audio` was left on, and a track that
appears when silence was asked for is refused.

**A real 16:9 canvas, at a size that is actually 16:9.** The published LTX canvases were 7:4
(448×256) and 11:7 (704×448) — near-widescreen, not widescreen. The smallest canvas on LTX's 64-px
snap grid that is *exactly* 16:9 is **1024×576**, now published with a duration of **2 seconds**.
Measured on this Mac: 49 frames, **9.36 GB** peak Metal, 351.6 s, clean output — receipt in
`qualification/ltx-video-results-16x9-20260927/`.

**Lance was tried at 16:9 and does not hold up.** 512×288 sits on Lance's own grid and renders
(25 frames, **11.0 GB**, 784 s), but the subject degrades badly at that width, and the card only
validates square configurations. So **no non-square Lance canvas is published**; the failure is
recorded rather than shipped as an option.

**Two smaller corrections.** Naming a canvas without a duration used to hand back LTX's default 4
seconds even where that canvas has no 4-second configuration — a request the lane's own validator
would refuse; the default is canvas-aware now. And the 16:9 receipts were moved out of `/tmp` into
`qualification/`, where evidence is supposed to live.

## 0.6.82 — 2026-09-27 · Lance runs, and the lane stops being LTX-shaped



### The second video model can run now

The smaller video model, Lance 3B (bf16), was listed but not runnable: its runner had not been
adopted into the app. It is now part of the build, with its own runtime, and it has made real clips
on this Mac — including one measured through the app's own video worker. Text-to-video at 256×256:
about three minutes for a two-second clip, and about 8 GB of memory in the mode a 16 GB Mac uses.

### What it needed, and what that changed

The two video models need **different Python versions**, which is why the app now carries two video
runtimes rather than one. The smaller model also needs a small helper file set (4.5 MB) beside its
weights; without it the model would try to reach the internet while loading. Both are packaged and
checked by content, like every other runtime in this app.

### Also fixed

The app used to describe every video model with the big model's sizes and frame rate. The smaller
model has its own — square frames at 12 per second — and now says so. And a memory measurement that
was reported as a few dozen megabytes (it was watching the wrong process) now reports the real
figure.

### Technical notes

**The second engine is adopted, and it needed a second interpreter.** `lance-mlx` declares
`requires-python >= 3.12` while `ltx_pipelines_mlx` needs 3.11 — opposite constraints, so
"model-agnostic lane" turned out to mean *two pinned runtimes and an interpreter per engine*, not
one tree with two packages. `packaging/lance-runtime/build.sh` builds the second tree (Python
3.14→3.12.14 + lance-mlx at `f8ecc5a8…` + MLX 0.31.2), pinned by content at
`LANCE_RUNTIME_SHA256 = 48dd2bd2…`, and the stager refuses anything else.

**Measured, twice, in the two modes the policy chooses between:**

| Mode | Config | Peak footprint | Wall |
| --- | --- | --- | --- |
| `relay` (what a 16 GB Mac gets) | 256×256, 13 frames | **8.11 GB** | 184.9 s |
| `parallel` (this 32 GB Mac, through the worker's own routes) | 256×256, 25 frames | **20.0 GB** | 329.5 s |

Both by sampled `footprint phys_footprint_peak` **in the process doing the work** — see the defect
below. The card's 16 GB claim stays `[V]`; 8.11 GB on a machine with no memory pressure is a floor.

**The lane stopped assuming LTX's shape.** Canvases, frame rate, frame grid and the latent ceiling
were module constants; a Lance request would have been offered 704×448 at 24 fps on an `8k+1` grid,
outside everything its runner was validated on. They are now per model: Lance publishes square
256²/512² at **12 fps** on a **`4k+1`** grid (measured: 16 frames in, 13 out), checked against its
own card's `n_lat ≤ 16,128` production bound rather than LTX's 30,000-token cliff.

**The bridge, and three defects it found.**

- `run_lance` spawns `runtimes/video-lance/bin/python3 lance_runner.py <spec>` and reads one JSON
  object per line. The runner's own CLI is a **stub** at the pinned commit, so the library API is
  the only surface.
- **The worker measured the wrong process.** Its footprint sampler watches the worker, but for this
  engine the memory is in the child: the first lane run recorded **57 MB** for a job whose real peak
  was ~8 GB, and would have published it. The bridge samples itself now, and its number wins; the
  job names the method.
- **A fixed key list swallowed the measurement.** The bridge reported the peak; `run_lance` copied
  five named keys and dropped it. The list is now explicit and holds the measurement fields.
- **The frame-rate parameter still published 24.** LTX's constant was hardcoded in
  `lane_parameters`, so the Lance default request failed its own validator — found by the first
  worker-alone run, which is what that run is for.

**Also in this release:** the Qwen2.5-VL processor is a **required companion package**
(`lance-3b-processor-qwen2-5-vl`, six files, 4.5 MB, captured at `66285546…`). The weights pack
ships only the tokenizer, so without it the pipeline reaches for huggingface.co at load time; the
first measured run proved a node can work offline with it installed. And
`packaging/video-runtime/normalize_records.py` no longer hardcodes `python3.11` — it walked past the
3.12 tree and would have left every RECORD hashing pre-rewrite scripts whose shebangs carry the
build path, which is exactly the machine-specific digest that file exists to prevent.

**Evidence.** `qualification/lance-video-worker-results-20260927-first/measurement.json` — job
`0f259095f44a4c03958e64119f435b47`, `done`, 256×256, 2.084 s, 276,726 bytes, one video track and
**no audio track** (Lance's t2v is silent, and the LTX-only audio check correctly did not reject
it). `_orchestra/evidence/lance-first-run-20260927.json` for the relay run. Gate: **1380 passed / 11
skipped / 1088 subtests**; `bash native/build.sh` green.

## 0.6.81 — 2026-09-27 · The lane published a canvas its own pack resizes

### A video was not the shape the app said it was

The video model works on a grid: both sides of the picture have to be a multiple of 64 pixels, and
the app was offering a size that is not on it. The model quietly rounded the picture down — a clip
asked for as 16:9 came back as 2:1 — while the job still reported the size that was asked for. The
sizes the app offers are now the sizes the model actually produces, and the app's own screens only
offer a shape and a size that go together, so a request it cannot honour is never presented as a
choice.

### The video test can run from the SSD

The Technical folder on the drives now carries a video command: double-click it on any Mac and it
measures one video generation through the app's real worker — how long it took, how much memory it
truly used, and the finished clip's real length and tracks. On a Mac below the model's published
memory tier it reports the refusal, which is that Mac's honest answer rather than a failure. The
same folder's second command measures the older standalone path for comparison, and the README
beside them says which to run and what to send back.

### Technical notes

**Measuring the lane from the SSD found this, and it is the sharpest defect in the video work so
far: the lane's *default* request was a canvas the lane does not produce.** The LTX pack's
two-stage pipeline floors each output dimension to a multiple of 64 before it generates
(`snap_output_dimensions(height, width, two_stage=True)` in the pinned runtime's
`ltx_core_mlx/components/patchifiers.py`: stage 1 runs at half resolution and is upscaled 2x).
`512x288` is not on that grid, so the published `288p` `16:9` canvas came back as **`512x256` —
2:1, not 16:9** — while the job's own acceptance still said `512x288`. Measured in the first run
of the new worker harness:

```
two-stage half-res snaps dims to multiples of 64; output will be 512x256 (requested 512x288)
```

`CANVAS_CLASSES` is now a table of **pairs on the pack's grid** — `256p` (`7:4` 448x256, `4:7`
256x448) and `448p` (`1:1` 448x448, `11:7` 704x448, the measured configuration) — and the model's
default is the canvas it qualified (`11:7` at `448p`), not a ratio the pack would resize. There is
no `16:9` entry and that is arithmetic rather than an omission: with both sides multiples of 64,
the smallest exact 16:9 canvas the two-stage path can produce is `1024x576`. `_assert_canvases_
are_snap_stable()` refuses to import a table off that grid, naming the entry and the size the pack
would substitute.

**The two parameter enums were never a set of canvases, and a consumer built the cross product.**
`lane_contract` now publishes `canvases`: every `(aspect_ratio, resolution)` pair, its exact pixels
and the durations that canvas accepts (from the same `canvas_durations()` the validator enforces,
so published and accepted cannot drift). The validator refuses a pair the table does not carry by
name. `tests/test_video_worker.py` walks the whole cross product and asserts every published
combination is accepted and every unpublished one refused — the invariant whose absence let this
ship.

**Why it mattered to a consumer, measured rather than reasoned:** Story Studio submitted `16:9`
with whatever resolution the picker offered. At `448p` the lane refused it (`448p is not published
for 16:9; that aspect ratio offers: 288p`), and at `288p` it accepted it and returned a 2:1 clip.
The gateway built its video variants as the cross product of the two enums and published
`aspect_ratios` from the union, so the picker offered pairs that do not exist. Both are fixed in
their own repositories in the same change set: the gateway builds variants from `canvases` with
each variant carrying its own ratio, and Story Studio sends the ratio of the row it priced.

**Also in this release — the video hardware test can run from the SSD, on a Mac that has only the
app.** The fleet's hardware step is the owner's, and the SSD kit carried chat and Z-Image commands
and no video one. `qualification/RUN-LTX-VIDEO-WORKER.command` drives the shipped worker (the
app's own `components/` and `runtimes/video`, no checkout), prints the machine, the tier the
manifest publishes and the expected verdict *before* the run — on a 16 GB Mac a refusal is that
test passing — and writes `measurement.json`. `ltx-video-worker-run.py` resolves its hub root from
`STUDIOHUB_HUB_ROOT`, then a checkout, then `/Applications/Studio Hub.app/Contents/Resources`, and
its results-folder guard now permits the folder the wrapper creates for the console log while
still refusing to overwrite a previous receipt (its first run found exactly that:
`FileExistsError` before the run started). `sixteen-gb-ltx-video-run.sh` picks the first runtime
candidate that can actually import `mlx.core` + `ltx_pipelines_mlx`; it used to take the first path
that exists and did not know about the app's runtime, so a node with the app installed was told
"the LTX runtime is not on this Mac" while one sat inside the bundle.

**Evidence.** The wrapper ran end to end on this 32 GB M2 Max with the app bundle's components and
runtime: 16:9 288p 2 s request, job `71865db1877c4e7dbd8918c9e8c9ff64`, `done`, 512x256 at 49
frames, 2.0417 s, 1 video + 1 audio track, 103.7 s wall, peak MLX 9,977,828,986 bytes, peak
footprint 15,386,290,304 bytes. The native Video page reads the same `canvases` list and snaps its
controls onto a published pair. Gate **1376 passed / 11 skipped / 1081 subtests**, `bash
native/build.sh` green.

## 0.6.80 — 2026-09-26 · The app's ffmpeg has no H.264 encoder

### The video tool the app shipped could not write video

The video model finishes its clip and hands the frames and sound to `ffmpeg`. The copy of `ffmpeg`
this app carried was built for audio only — it has no H.264 encoder, which is the format a video
file needs — so the clip was made and then could not be written. The video worker now carries its
own pinned `ffmpeg` (the same one Story Studio uses for its final assembly), and if it ever cannot
find a capable one the app says so before offering the model rather than after a five-minute
generation.

### Technical notes

**The mux fix in 0.6.79 found the second half of the same problem.** With the Voice runtime's `bin`
on PATH the worker reached ffmpeg and failed inside it:

```
ffmpeg encode_single_frame failed: ... Unrecognized option 'preset'.
```

The Voice runtime's ffmpeg is built from source with a minimal configuration — its encoder list has
`mpeg4` and no H.264 at all. Worse, it **answers `-h encoder=libx264` with exit 0** and the sentence
*"Codec 'libx264' is not recognized by FFmpeg"*, so "there is an ffmpeg" is not the question the
pack needs answered: it names `libx264`, and the output is what says whether the build has it.

`packaging/video-runtime/build.sh` now fetches the **same pinned ffmpeg 8.1** Story Studio already
ships for Final Video Assembly (`osxexperts.net`, arm64, sha256
`ebb82529562b71…` — Story's own `bundled/ffmpeg/MANIFEST.json` records the same URL and hash) plus
its matching ffprobe, verifies both hashes on every build, and **refuses to build a runtime whose
ffmpeg cannot answer for `libx264`**. The binaries land in `runtimes/video/bin`, which is first on
the worker's PATH; the Voice runtime stays behind it for everything else.

`generation.muxer_problem()` now asks the same question at catalog time — no ffmpeg, or an ffmpeg
without `libx264` — and publishes `ready: false` with the sentence instead of advertising a model
that generates for five minutes and then fails at the last step. The answer is cached, because a
catalog read must not fork a subprocess per model.

**Evidence.** The runtime's own ffmpeg round-trips `encode_single_frame`/`decode_single_frame`
(704×448 RGB in, a 1,803-byte H.264 frame out, decoded back to the same shape), `muxer_problem()`
returns None with it on PATH, and `tests/test_video_worker.py` covers all three states — absent,
present without `libx264`, and video-capable. Gate **1373 passed / 11 skipped / 1057 subtests**.

## 0.6.79 — 2026-09-26 · The last step of a video job was missing its muxer

### A video job could make the whole clip and then throw it away

The video model hands its finished frames and sound to a small helper program, `ffmpeg`, to write
the MP4 file. This app already includes that helper, but the video worker was not being told where
it was — so the clip was generated in full and then the job failed at the last step. The worker now
looks where the app keeps `ffmpeg`, and if it ever cannot find it the app says so *before* offering
the model, instead of after a five-minute generation.

### A cancelled video job could not be checked

If a video job's outcome was uncertain, pressing the check button answered with a message about
images and could not settle it; the job held its machine until it was discarded. Video has its own
check now.

### Technical notes

**The pack generated the whole clip and then failed at the mux.** The LTX pipeline decodes the video
and audio latents itself and hands the raw frames and samples to **`ffmpeg`** (`utils/media_io.py`,
`find_ffmpeg()` → `shutil.which`). The packaged Video worker's PATH was its own runtime plus
`/usr/bin`, and the app's `ffmpeg` lives in the **Voice** runtime's `bin` — so the worker generated a
complete clip and then died with the pack's own message:

```
ffmpeg not found on PATH. Install it with: brew install ffmpeg
```

Measured 2026-09-26, on the first through-the-chain run. `packaging/video_worker.py` now puts
`runtimes/voice/bin` on the Video worker's PATH, after its own runtime. Nothing is duplicated: every
Hub build already carries `ffmpeg` and `ffprobe` there (21.7 MB each, the same pair the Voice,
Music and Render lanes use), and a Hub bundle without a Voice runtime is refused at startup. The
alternative — copying them into `runtimes/video` — would add 43 MB and a second licence copy.

**And the catalog says so when it is missing.** `generation.muxer_problem()` asks the same question
the pipeline asks, and a worker that cannot see `ffmpeg` publishes `ready: false` with that sentence
instead of advertising a model that will fail at the last step.

### Video items can be reconciled

An uncertain **video** item had no reconciler: the route fell through to the Image one and answered
`unknown Image batch` — measured on the first uncertain video item this Hub ever held (a cancelled
job whose machine slot stayed pinned until it was discarded). `broker.reconcile_video_item` is the
modest missing entry; the reconciler itself is modality-agnostic and already validated video
receipts. The route's silent Image fallback is gone with it: a modality with no reconciler is named
rather than described as Image.

**Evidence.** `tests/test_video_worker.py` 24 passed / 19 subtests; full Hub gate green.

## 0.6.78 — 2026-09-26 · The video lane's free-memory floor deadlocked it

### Video jobs could sit waiting for memory that never came free

The first real video job sent through the whole chain never started: the Hub held it waiting for
16.5 GB of *free* memory, which a healthy 32 GB Mac does not keep aside — macOS uses that memory for
file cache, and it hands it back the moment a program needs it. The wait was for a number that could
not arrive.

The gate is now 8 GB, which still refuses to start work on a Mac that is genuinely full. The rule
that decides whether a Mac can run the big video model at all is unchanged: 24 GB of total memory.

### Technical notes

**Found by the first real chain job, not by a unit test.** The gateway submitted one
image-to-video request through the whole path and the Hub held it `queued` with:

```
governor_note: video: released 5 idle sibling(s); waiting for memory: needs ~16.5GB, ~14.2GB free
```

The item never reached the worker. `min_free_memory_gb=16.5` was copied from the measured *process
footprint* (16.38 GB), and that is not a free-memory requirement: on a healthy 32 GB Mac, available
memory (free + inactive + speculative) sits around 13–20 GB because macOS fills inactive pages with
file cache — and those pages are exactly what the footprint is made of, reclaimable on demand. The
Hub answers an unmet free floor with **wait, not skip** (`broker._memory_gate`), so the lane waited
for memory that was never going to arrive while it had already released every idle model.

The floor is now **8 GB**: a guard against starting into a genuinely full machine, with room for the
run that was measured here to begin. The Image lane recorded this same lesson when it lowered
Z-Image's floor from its 7.1 GB footprint to 3.0 GB, and `tests/test_video_worker.py` now pins the
shape: the live floor must be below the model's tier *and* below the measured footprint.

**What would raise it:** a hardware measurement showing a start below 8 GB free swapping through the
load on a 24/32 GB Mac — which is what the 16 GB/24 GB hardware tests are for.

## 0.6.77 — 2026-09-26 · The text encoder is a dependency, not a video model

### A support file no longer looks like a model

The video model needs a second file — its text encoder — and the app was listing that encoder as if
it were a video model you could pick. It has no clip to make and no memory figure of its own, so it
showed as unavailable for a reason that did not apply. It is now treated as what it is: a file the
video model loads, not a model in the list.

### Technical notes

**Found on the installed 0.6.76 Hub, in the live catalog.** The Video worker published a catalog row
for *every* `video-ltx` manifest it read, and the LTX pack's text encoder
(`gemma-3-12b-it-4bit`) is one — so the encoder appeared as a pickable video model on this Mac with
`memory_eligible: false`, and the gateway withheld it with *"this Mac does not have the memory the Hub
says this model needs"*: a true sentence answering a question nobody asked. The Hub's own package row
already marks it `type: dependency`; the row was coming from the worker.

`GenerationManager.packages_without_a_runtime_model` now excludes any package another manifest
`requires`, through the one shared reader (`model_packages.required_packages`), so a companion gets no
model row at all — the same shape the Voice catalog has always had, where a tokenizer is not a model.
`tests/test_video_worker.py` pins both halves: a required companion is not published, and a manifest
nothing requires still is.

**One message improved in the same change.** `validate_request` refused an unpublished duration with
*"Duration must be one of [2, 4] seconds"* — naming the accepted set but not the value that was
refused. It names both now, which is the rule every other refusal in this lane already followed.

**Evidence.** `tests/test_video_worker.py` 21 passed / 19 subtests; full Hub gate green.

## 0.6.76 — 2026-09-26 · The Video lane exists

### Video is here

There is a **Video** page in the Create section now, and it is a real Studio: pick the video model,
describe the shot, choose from the sizes and durations that model itself publishes, and generate.
The first model it runs is **LTX-2.3 22B** — the big one, for this Mac and the other 24/32 GB
machines — and it makes a short clip **with its own soundtrack**.

Image-to-video is the other half and it comes from the app that owns your scene images: **Story
Studio** uploads the exact scene and asks this Hub to animate it. Both routes run the same lane on
this Mac.

- **The 22B model needs 24 GB and this build says so before it offers it.** On a 16 GB or 8 GB Mac
  the model is simply not offered, instead of being offered and then failing to load. The measured
  numbers behind that rule are in the changelog: a 4-second, 704×448 clip with audio took about five
  minutes and peaked at a 16.4 GB process footprint on the 32 GB Mac.
- **A smaller model is prepared but not switched on.** **Lance 3B** is the one meant for the 16 GB
  machines. Its download and its memory tier are recorded, but the runner it needs is not part of
  this build yet, so the app lists it honestly as not ready rather than pretending.
- **A clip's length is measured, not guessed.** Each finished clip reports how long it actually runs,
  and the file it produces is checked before it is offered for saving.
- The **Jobs** page now has real video rows, and the sidebar no longer has a "Coming later" section —
  every Studio has an engine.

### Technical notes

**There was no video lane at all.** `components/` was chat, hub, image, music, subtitle and voice;
the Hub's broker had mapped a `video` modality to `/api/generate/txt2video` and classified it
`durable_async` since it was written, and the deadline floor was already 7,200 s — but nothing
served it, so the modality was a route to a worker that did not exist. It exists now, and the parts
that were already there are used rather than restated.

### The worker — `components/video/`

One model-agnostic registry, two engines, two memory tiers, and the two doors the broker already
speaks:

- **Text-to-video** (`POST /api/generate/txt2video`, JSON) and **image-to-video**
  (`POST /api/generate/video2video`, multipart `file`) — the exact routes `broker.MODALITY` and the
  broker's `img2video` reference path call. A job is **accepted before the result exists** and
  returns its receipt immediately, which is what `RETRY_CLASS_DURABLE_ASYNC` requires: a five-minute
  job must survive a Hub restart without being blindly replayed.
- **The pack's own loader, before the pipeline is constructed.** `ltx_core_mlx.apply_quantization`
  applies ONE bit width to this mixed 2–8-bit pack and dies on a shape error deep in
  `quantized_matmul`; the pack ships the loader that knows, and the job receipt names it
  (`pack_generate.py:_patch_pipeline_quantization`).
- **Block streaming is never enabled.** It fails on this pack on two keys the streaming block does
  not carry; the field exists with one allowed value so a future edit has to be deliberate.
- **The artifact is verified, not assumed.** The MP4's own box structure is read: `ftyp`, a video
  track with non-zero dimensions, a positive duration, and — for a pack whose measured runs carry
  audio — an audio track. The bytes are hashed and served from the worker's own route.
- **Memory is measured the way the pack measures it.** The receipt carries MLX's Metal high-water
  mark, the process RSS, and **`ri_phys_footprint` sampled at 1 Hz** (`libproc`, the same number the
  Lance card calls `ri_phys`); `mx.get_peak_memory()` alone under-reports the process.

### The tier, which is the load-bearing part

A model the machine cannot load must not be offered, and the Hub's total-memory floor is
deliberately never in force (`memory_admission`'s own doctrine). So the **worker publishes the
verdict per host**: below the model's qualified tier it publishes `memory_eligible: false`, which is
what the gateway's `withhold_reason` already reads. The failure becomes a routing decision instead
of a node-side load error.

| Model | Task | Tier | Evidence |
| --- | --- | --- | --- |
| **LTX-2.3 22B MLX mixed 2–8 bit** (`baa-ai/LTX-2.3-22B-RAM-12GB-MLX` rev `d8a9d2f6081c`) | I2V + T2V, with audio | **24 GB** | measured here `[M]` |
| **Lance 3B Video MLX bf16** (`mlx-community/Lance-3B-Video-bf16` rev `b70ba0a53c14`) | T2V | **16 GB** | vendor claim `[V]`; **runner not adopted** |

The LTX measurement, on this 32 GB M2 Max: **704×448, 97 frames, 24 fps, 4.04 s of output with one
video and one audio track, 304.0 s wall through the worker's own job path** (298.5 s standalone),
peak MLX Metal 14,163,847,570 bytes, peak `ri_phys` footprint 15.71 GB sampled at 1 Hz (the
standalone run's externally-measured process footprint was 16.38 GB). That is why the tier is 24 GB
rather than the vendor's 16 GB: MLX had no memory pressure to trim on a 32 GB host, so 16.38 GB is a
floor, not a worst case. Lance is the small tier's model; its manifest and 16 GB tier ship now, and
the worker publishes its row with `execution_ready: false` naming the `lance-mlx` adoption step —
an absent row would be invisible, and a row that claimed it runs would be a lie.

### The runtime is its own tree, and it is pinned by content

`ltx_pipelines_mlx` needs Python 3.11 while the base runtime is 3.12, so Video carries
`runtimes/video`, built by `packaging/video-runtime/build.sh` from a pinned interpreter plus
`requirements.lock.txt` and the `ltx-2-mlx` checkout at
`3b2e77fd73af5f08961f4436ae979c0579d5deed`. `packaging/stage_voice.py` refuses to stage a tree whose
digest is not `VIDEO_RUNTIME_SHA256`, the same rule the Image runtime learned the expensive way. The
tree is made **read-only** after the build, because a plain interpreter run inside it would write
`__pycache__` back and move the digest the stager pins — a used runtime must not become an
unstageable one.

### What else moved

- **Manifests**: `ltx-2-3-22b-ram-12gb-mlx` (11 files, 19.12 GB, `requires_packages`
  `gemma-3-12b-it-4bit`), the text encoder companion (13 files, 8.07 GB) and `lance-3b-video-bf16`
  (10 files, 15.63 GB), all captured from their pinned revisions. The capture tool gained a
  `&limit=100` on its tree URL — the Hub paginates at 50, so a large repository could silently lose
  its weights — which is why every existing manifest's canonical bytes moved in the same pass.
- **The companion is resolved by installed path**, so an offline node never reaches for the network,
  and the receipt records which path the runner used.
- **Hub registration**: `video` is a routed modality and a real worker (`vocabulary.py`),
  `video-ltx`/`video-lance` are engine routes, both video doors are fleet-reachable, and the broker
  validates a video completion's receipt (`output_url`, `media_type: video/mp4`, a positive frame
  count and clip duration) the way it validates Image's and Music's. A finished video item now
  carries its measured length, frame count and audio claim for the operator.
- **The native app**: Video moved out of "Coming later" into Create. The page lists the lane's own
  published options — aspect ratio, size class and duration are read from the model's `lane_contract`,
  never a ladder in the app — renders the below-tier sentence like every other page, files real clip
  rows in Jobs, and shows the measured clip length. Image-to-video stays Story Studio's surface, which
  is the app that owns scene images.
- The page switch lost its `default: unavailableWorkspace` fallback: with every `StudioPage` case
  wired, the compiler now proves what the fallback only reported at runtime.

**Evidence.** Hub gate **1347 passed, 11 skipped, 1038 subtests, exit 0**; `bash native/build.sh`
green (boundary, API-contract and lifecycle checks); the worker-alone run's full receipt is
`qualification/ltx-video-worker-results-20260926-210721/measurement.json`.

## 0.6.75 — 2026-09-26 · The installer can no longer leave a broken app, and the disk stops ratcheting

### Installing a new version can no longer half-install it

While installing the previous version, the Mac ran out of disk space part-way through copying the app.
What was left behind looked installed but was not: the app had lost the two files that make it an app
at all, and the automatic undo that should have put the previous version back instead put it *inside*
the broken remains. The installer reported a failure, but nothing could tell you what state you were in.

Three things changed:

- The installer now **refuses to start if there is not enough room**, and it tells you how much it
  needs. It checks before it quits the running app, so a full disk no longer interrupts you just to
  report a problem.
- **The undo can no longer make things worse.** It clears the incomplete copy properly (those runtime
  files are deliberately read-only, which is what defeated the old delete), and it puts the previous
  version back only once the way is clear. If it cannot, it stops and tells you exactly where your
  previous version is, instead of claiming a success.
- **It checks the whole app arrived**, by counting, rather than looking for one file that a partial
  copy also has — which is why a half-copied app previously passed the check.

### The disk stops filling up on its own

Every version you install writes a full copy of the app into the build folder and a disk image beside
it, and the old disk images were moved to `_trash/` — which is on the same disk, so nothing was ever
actually reclaimed. That is what filled the Mac: the build folder alone was holding **100 GB**, 70 GB
of it thirty-two complete copies of past versions, each one rebuildable from the source in a couple of
minutes.

There is now a step that keeps the version you are running, keeps the most recent one, and moves the
rest to one of your SSDs — checking every file against its checksum first, so nothing is removed until
a verified copy exists elsewhere. It does nothing at all if no drive has room.

### Technical notes

**The install that filled the disk left `/Applications/Studio Hub.app` with no `Info.plist` and no
`MacOS`, and the rollback is what made it unrecoverable.** Measured: the copy stopped after ~1.4 GB of
a 2.9 GB bundle, `rm -rf "$TARGET"` could not clear the wreckage — the image runtime's
`runtimes/image/lib/tcl9` directories are `dr-xr-xr-x`, and unlinking an entry needs write permission
on its *parent* — and the rollback's `mv "$OLD" "$TARGET"` then put the previous app **inside** the
wreckage instead of over it, because the target still existed. `install_local.sh` exited 1 and every
later check was looking at a directory that was not an app.

Three changes, each proved to fire on that defect by `tests/test_install_local.py`:

- **It refuses to start when the copy would fill the disk**, before anything is moved — and before the
  app is quit, because being told there is no room is not a reason to interrupt the app someone is
  using. The check is the stage's size plus half again against the free space it can see.
- **The rollback cannot nest the previous app.** It `chmod -R u+w` first, then verifies the target is
  actually gone before restoring, and if it is not, it stops and says where the previous app is rather
  than reporting a success it cannot verify.
- **Completeness is counted, not sampled.** The old guard checked one file — `runtimes/voice/bin/python3`
  — which a copy that stopped inside `runtimes/` still satisfies, which is exactly how a bundle with no
  `Info.plist` passed it. It now compares entry counts against the stage.

`STUDIOHUB_INSTALL_TARGET` and `STUDIOHUB_INSTALL_DATA` exist so those two guards can be tested without
touching `/Applications` or the operator's real readiness markers; production passes neither.

**And the reason the disk filled: `.build/`, not `_trash/`.** Every release staged a ~2.9 GB bundle and
built a ~1.3 GB DMG, and the previous DMG moved into `_trash/dist/` — on the same volume, so nothing was
ever reclaimed. The owner cleared `_trash/` by hand and recovered 38 GB; the audit then measured
**`.build/` at 100.9 GB, of which 32 staged bundles held 70.7 GB**. `packaging/prune-build-scratch.sh`
is the step that stops it recurring: it keeps the stage of the release currently in `VERSION` (read
from each stage's own `release-manifest.json`, never from a timestamp — `chmod` updates a directory's
mtime, and a "newest by mtime" pass proposed moving the 0.6.74 stage), keeps the newest of the rest,
moves the remainder to a kit drive, verifies each copy by sha256 manifest on both sides, and only then
removes the local one. It refuses to act at all when no drive has room.

## 0.6.74 — 2026-09-26 · The Voice worker's loops start with the app, not with the import

### Voice Studio tidies up only while it is actually running

Voice Studio frees a model that has gone idle, and clears out old generated audio according to your
retention setting. Both of those chores had a flaw with the same cause: they started the moment the
Voice program was *loaded*, rather than the moment it started *running*.

Two things that caused, both now fixed:

- A test run could start those chores too. The idle timer would unload a model that a test had just
  pretended to load, which made a test fail occasionally for no visible reason — it was reported more
  than once as a slow test, and it was never slowness.
- The cleanup timer reads your real output folder, so a test run could have deleted real generated
  audio once it was older than your retention setting. Your actual Voice Studio was never at risk —
  it is told not to run its own cleanup, because the Hub does that — but a test was not.

Neither chore starts until Voice Studio is genuinely serving, which is how the Music and Subtitle
parts of the Hub have always done it.

Nothing about your settings, your voices or your generated files changes, and idle unloading and
cleanup behave exactly as before while the app is running.

### Technical notes

**Both of the Voice worker's background loops — idle model release and output retention — were
started by `import backend.main`.** Every process that imported that module got them, a test run
included, and two real things followed. Neither was a test-only problem.

- **The idle-release ticker reached into a test's own fakes.** `test_memory_policy.py::test_memory_api_frontend_and_process_title`
  failed with `last_release_details.released is False`. It had been recorded as flake — *"it is a
  timeout, not a failure"* — and it was not a timeout. The 5-second ticker released the managers the
  test had just injected, through the test's own settings file, so the test's own
  `POST /api/memory/release` then reported *correctly* that nothing was loaded. **Measured directly,
  with the test doing nothing at all:** `gen.releases` 0 → 1 after seven seconds, and the same POST
  then answered `released: False`. Every assertion in that file that counts releases was racing the
  ticker, which is why the gate was green most of the time and red sometimes.
- **The retention loop enforced the operator's policy against the real output directory**, 60 seconds
  after import and hourly after that. A test session could delete real generated audio in
  `~/Library/Application Support/StudioHub Standalone/voice/output`. The packaged worker is protected
  from its own loop by `STUDIOHUB_OUTPUT_POLICY_OWNER=hub`, which `voice_worker.py` sets — a test run
  is not, and that variable is not what a reader would check.

**Both loops are now registered on the app's `startup`.** This is the pattern the sibling Music and
Subtitle workers already use, whose comment says it outright: *"The packaged worker calls this once; a
test that builds a second app gets its own clock instead of the first one's."* Voice was the outlier.

**Production still starts them, and that was checked rather than assumed.** `voice_worker.run_server`
builds `uvicorn.Config(app, …)` with no `lifespan` argument, so uvicorn's `auto` mode runs startup
handlers, and the installed worker's own log carries `Application startup complete.` — a line only the
lifespan path writes. `main.py` already registered `_reconcile_auto_update_scheduler` there.

**`tests/test_background_loops_start_with_the_app.py` pins both halves**, because "starts on import"
and "starts at all" are different failures: a fresh interpreter must import the module and find
neither thread, and the startup handler must be registered and wired to the worker's real managers and
output directory. It asserts the wiring without starting the real threads — starting them would leave
a live ticker running for the rest of the suite and make `test_memory_policy.py` racy again, which is
the defect the file exists to prevent.

## 0.6.73 — 2026-09-26 · Chat gets its margin back, and the video runner stops wasting runs

### The Chat page gets its margin back

The box you type into sat flush against the top and left of the window, while everything below it —
the model picker, the buttons — was properly inset. Chat was the only one of the five Studio pages
without that outer margin; Image, Audio, Subtitle and Music all had it.

That is fixed, and the test that should have caught it now does: it checks the margin on all five
pages. Every other check in that test passed while this was wrong, which is why nobody noticed.

### The video runner stops wasting runs

Two things went wrong while testing video generation, and both cost a full attempt before anyone
could see why.

- It picked up **a half-finished copy of the model** sitting on the internal disk instead of the
  complete one on the drive. The error it produced said the model might be corrupt, which sent us
  looking in the wrong place. It now prefers the complete copy, and says so up front if the only copy
  it can find is unfinished.
- It turned on a memory-saving mode that **this particular model does not support**. The mode fails
  immediately rather than saving anything. It is off by default now — the model's own configuration
  is the one that works.

### What the video test actually measured

One 4-second clip with sound, on this 32 GB Mac:

| | |
|---|---|
| Time | **5 minutes** |
| Picture | 704×448, 4 seconds, with audio |
| Peak memory | **16.4 GB** |
| Swap used | 3.9 GB |

**Worth knowing plainly: 16.4 GB on a 32 GB machine is more than a 16 GB Mac has in total.** That
does not prove it cannot run there — this was measured on the big machine, where nothing was forcing
it to economise — but the "fits in 16 GB" claim is not supported yet, and the next test that matters
is on one of the 16 GB Macs rather than another one here.

### Technical notes

**Chat was the only one of the five workspace pages without an outer margin.** The owner, looking at
it beside the others: *"the place to enter the text and stuff doesn't have a proper margin."* Exactly
so — Image, Audio, Subtitle and Music each wrap their split view in `.padding(28)`; Chat never did, so
its form column met the window's top and left edges while the model picker below it sat correctly
inset. The form column's own `.padding(.trailing, 20)` cannot supply it: that insets the content
*inside* the column, not the column from the window. **`test_audio_layout.py` now pins the outer
margin on all five pages, and it is the assertion that was missing** — every other check in that test
passed while this was wrong.

**Two defects in the LTX video runner, both of which cost a run each.**

- **It preferred a partial copy of the model over a complete one.** `~/StudioHubModels` came before
  the drives in the search order, so it picked a **10 GB unfinished rsync** over the complete
  **17.81 GiB** copy on NOVA. The failure reads as a corrupt model — `[load_safetensors] … invalid
  data offsets … Perhaps an incomplete download or corrupt file?` — when the truth is that the copy
  is unfinished. The drives now come first, and a copy under 90% of the package's known size says so
  **before** the run rather than 23 seconds into it.
- **Block streaming is now opt-in, because it does not work with this pack.** The research had called
  `low_ram_streaming` *"the cheapest route to a real 16 GB lane"*, so the runner passed it always. On
  the baa-ai pack it fails before generating: `ValueError: Received 2 parameters not in model:
  video_to_audio_attn.to_gate_logits.{biases,scales}` — the streaming block does not carry those keys
  and the pack never enables streaming itself. **That hope is now measured rather than assumed.**

**And the measurement itself, which is what the owner asked for** — one image-to-video generation on
this 32 GB M2 Max, through the runner, so the process footprint is captured as well as MLX's own
counter:

| | |
|---|---|
| Wall | **298.5 s** for 704×448, 97 frames (4.0 s of video, with audio) |
| Peak MLX Metal | **13.19 GB** |
| Peak process footprint | **16.38 GB** |
| Maximum resident set | **9.91 GB** |
| Swap at end | 3,852 MB of 5,120 MB |

**The 16 GB claim is not supported by this, and the honest reading is that it is in trouble.** A
16.38 GB peak on a 32 GB machine — where MLX has no pressure to trim — is more than a 16 GB Mac has
in total, before macOS takes its share. That is one run on one machine and it does not *prove* the
16 GB tier fails; it does mean the pack's "12 GB / 16 GB" reach is unproven and the margin is
negative, and that the next run that matters is on the 16 GB hardware rather than another one here.

## 0.6.72 — 2026-09-26 · The limit's provenance is a token, not a sentence

### The app can now say *whose* number a limit is

Every model publishes an output limit, and the question that matters is where it came from: the
model's own files, or somebody's sensible fallback. This Hub has always written the answer down in a
sentence — *"worker default: the checkpoint ships no generation_config.json"* — but a sentence is for
a person to read, and the piece of software that shows it to you cannot act on one.

So the gateway in between had to decide for itself, and it decided wrong: it described **Qwen3.5-9B's
16,384-token ceiling as a number the model stated**, when 16,384 is this Hub's own fallback for a
checkpoint that declares nothing. If you had seen that number labelled as the model's own, it would
have been this.

Each limit now says what kind of number it is in a form software can read, so the gateway passes it
along untouched and what reaches the screen is the truth: the checkpoint said it, this Hub packaged
it, or this Hub fell back to it.

Everything else in this release is the same Hub as 0.6.71.

### Technical notes

**`model_limits` said where each number came from in a sentence, and a sentence is not something a
consumer can branch on** — especially this consumer, which is two hops away with a gateway in
between that must *relay* rather than interpret. Without a token the gateway had to answer for
itself, and it answered `provider` for every stated value. The live row read:

```
max_output_tokens: 16384
max_output_tokens_source:        "provider"
max_output_tokens_source_detail: "worker default: the checkpoint ships no generation_config.json"
```

**The two fields contradicted each other, and the machine-readable one was wrong.** 16,384 is this
Hub's own `FALLBACK_MAX_OUTPUT_TOKENS` — the number a checkpoint that states nothing is given — and
Story Studio reads the token. Their row says what it cost: *"16,384 currently renders as if the model
stated it."*

**Each limit now carries `source_kind` beside its sentence**, and both are set in the same branch, so
they are one decision and cannot drift apart: `model` (the checkpoint's own file states it) ·
`curated` (this Hub's packaged metadata, not the checkpoint's word) · `worker_default` (this worker's
fallback) · `unknown` (nothing states it, and `value` is `null`). The vocabulary is the consumer's,
because the consumer is the one doing the labelling.

The sentence stays. A token is what a program branches on; the sentence is what a person reads, and
losing either is a different defect from the one this fixes.

**GatewayKH relays the token** rather than matching a word in the sentence — which is what they asked
for, and three of our defects came from matching a name where a fact could have been published. Their
half shipped as **0.2.48**.

**Verified:** the cross-repo half is pinned on their side by `tests/test_hub_limit_provenance.py`
(9 tests, proved to fail 9 of 9 on the defect); here, `tests/test_chat_worker.py` now pins
`source_kind` on both the checkpoint-stated and the worker-default case — the second being the number
that used to leave this Hub mislabelled.

## 0.6.71 — 2026-09-26 · Silence became visible to the operator too

### A job that has gone quiet now says so on the page

When a model stops reporting in the middle of a job, the Hub has been able to see it since 0.6.70 —
it notices silence in about two minutes, however long that job's ceiling is. That is the whole reason
a thirty-minute deadline is safe: the deadline can be generous about honest work while the silence
check stays impatient.

**But you could not see it.** A job that had stopped reporting looked exactly like one that was
working, because the app was not reading the number the Hub was already publishing.

Now the Jobs row says it, right under the progress bar:

> **No progress for 4 min 12 s — still held, not cancelled**

Two things worth knowing about that sentence.

- **It never cancels anything.** A quiet job is very often just a slow one — a long render, a model
  still loading. The Hub's rule is that a stall is *shown* and never acted on, so a job that has gone
  quiet keeps running and nothing is thrown away on the strength of this.
- **It tells you whether to keep waiting or to go and look.** Two minutes of silence on a long video
  is ordinary; twenty is not. Now you can tell the difference without watching a bar that has not
  moved.

Everything else in this release is the same Hub as 0.6.70.

### Technical notes

**A stall the Hub could see, nobody watching the app could.** 0.6.70 made a quiet worker *visible* —
`stall.py` notices silence in about two minutes whatever the deadline is, which is the whole reason a
30-minute ceiling is safe rather than merely long — and the Hub has published `stalled_seconds` on
every job row and every fleet machine row since. **The app read none of it.** `HubJobSummary` decoded
two fields, `id` and `modality`, so the number arrived on the wire and was dropped on the floor: a
scheduler could see a stalled job and the person watching the Jobs page could not. That is the SH-3
shape exactly — *a two-ended invariant does not see a hole in the middle* — where both ends are true
and the sentence cannot travel between them.

**All three links are built, and one test pins all three**, because any one of them alone looks like it
works. The **producer**: `broker.batch_summary` states it, and *both* job routes serve it —
`/api/hub/jobs` lists summaries and `/api/hub/jobs/{id}` is `_summary(batch)`, that same function plus
`items`. The **wire**: `HubJobSummary`, `HubJobDetail` and `NativeJob` name `stalledSeconds`, so
`convertFromSnakeCase` actually maps it, and `mapHubJob` passes it through. The **screen**: the Jobs
row draws *"No progress for 4 min 12 s — still held, not cancelled"* under the progress bar.

**It is advice, and the wording is pinned to the rule.** `stall.py` is explicit — *"What it must never
do is kill the job."* A merely-quiet job is one a long deadline exists to protect, so the row says
what is happening and promises nothing about the job ending.
`tests/test_stall_visibility.py` fails if the sentence stops saying the job is still held, and fails
if `stall.py`'s own contract is reworded out from under it.

**Verified:** `pytest tests/` **1344 passed, 11 skipped, 1000 subtests, exit 0** (was 1336/998 — this
file's 8 tests and 2 subtests), and `bash native/build.sh` green with the boundary, API contract and
lifecycle checks all passing. **The guard was proved to fail on the defect:** restoring the original
two-field `HubJobSummary` fails it with *"the Hub publishes it and the app drops it, which is the
defect this file exists for"*.

## 0.6.70 — 2026-09-26 · One deadline became four, and silence became visible

### Long jobs stop being cut off, and a stuck one stops being invisible

Video clips take a long time to make — fifteen minutes to three quarters of an hour on these Macs —
and the Hub used to give every job the same fifteen-minute ceiling. So a video would be cut off
part-way through with nothing to show for it.

The ceiling is now set per kind of work. **Video gets two hours, chat gets thirty minutes, and
images, voice, subtitles and music keep fifteen** — so a stuck subtitle job is still noticed quickly
instead of hiding behind a long video's allowance.

Chat also gets a bigger output budget: **16,384 tokens instead of 4,096**, in the same change,
because the two numbers are one ceiling and moving either alone would break the other.

### If a job goes quiet, you can now see it

A Mac that hangs or drops off the network mid-job used to leave you waiting with no way to tell a
slow job from a dead one. The app now notices **silence** — a job that is running while its Mac has
stopped reporting — and marks how long it has been quiet. **It never cancels the job**, because a job
that is merely slow is still work you asked for; it tells you, so you can decide.

### Long narrations are no longer cut off

A narration part of about seventeen minutes was being stopped part-way through, because the voice studio shared the same fifteen-minute ceiling as everything else. **Voice now gets thirty minutes**, so long-form narration finishes. It matches chat, which also got thirty minutes in this release.

### Z-Image-Turbo now runs on the 8 GB Macs

The 8 GB machines were previously told Z-Image was too big. They were wrong about it, and now it has
been measured: on an 8 GB M1, Z-Image-Turbo produces a 1024x1024 image in about six and a half
minutes and a 1280x720 one in about five and a half, in low-memory mode.

**Expect it to use some disk paging at that size, and expect those times.** The bigger Macs remain
the comfortable place to run it, and the app still recommends 16 GB.

### Technical notes

**The Hub's job deadline is no longer one number.** It was a single flat 900 s on every lane. The
owner asked the question that ended that: *"if we raise it too high for, say subtitle and it failed,
could we find out or we have to wait 2 hours to findout?"* A shared ceiling is either too short for
the longest job or so long that a broken short one goes unnoticed. `deadline_seconds` is now derived
per **generator** by one function, `standalone.deadline_floor_seconds(modality)`, which feeds **both**
the value published in every lane contract and the timeout the proxy enforces — so what a scheduler
reads is still what this Hub holds the job to. Video **7,200 s**, chat **1,800 s**, image, voice,
transcription and music **900 s**, and a modality nobody classified gets the 900 s floor because a
short deadline can only cut a job short.

**Chat's output fallback moved with it, because the two are one ceiling.** GatewayKH measured the
pair: 16,384 tokens at 1,800 s is 76/65/51 % on M1/M2/M4 — the same margin 8,192 at 900 s gives
today — while 16,384 at 900 s times out on every chip including the M4. So
`FALLBACK_MAX_OUTPUT_TOKENS` went **4,096 → 16,384** and the chat floor went **900 → 1,800** in the
same build, with the arithmetic recorded beside both. **A model that states its own smaller ceiling
still binds** — a fallback can never raise a lane above the window its own model publishes.

**Video was classified for a lane that does not exist.** It sat at `NON_DURABLE`, reasoned from
*"Video and Render answer a terminal result for the POST itself"* — which describes the **legacy**
Video Studio's synchronous POST, not a lane whose generations run 15 to 45 minutes. Under that class
a node dying at minute 40 of a 45-minute job lost the job outright: no `uncertain` state, no retained
attempt, no re-dispatch, while the identical failure in the Image lane was retained and requeued.
Video is now **`DURABLE_ASYNC`**, which is a requirement on the worker as much as a label.

**A stall is now visible instead of merely long.** `components/hub/backend/stall.py` decides whether a
worker that has gone quiet while holding a job is stalled, from the `activity_received_at` receipt
the monitor already collects — and it is wired into every job row the Hub builds as
`stalled_seconds`. It fires only for active states, never for a job that has not started, never for
a terminal one, and never for a studio with no receipt at all. **It does not kill the job**: killing a
merely-quiet job would lose work the long deadline exists to protect. This is the piece that makes a
30-minute ceiling safe rather than just long.

**Z-Image-Turbo 6B MLX 4-bit is qualified on 8 GB.** The owner ran it on an **Apple M1 Mac mini
(`Macmini9,1`), 8.0 GB, macOS 26.6.2** with **mflux 0.20.0**: low-RAM completed 1024x1024 in 392 s
and 1280x720 in 336 s, 5.70 GB peak MLX and 5.85 GB footprint. The manifest's floor moved **16 → 8**
and its status **unqualified → qualified** — through `capture_model_manifests.py`, its producer,
not by hand. **The engine default also completed on that Mac, and that must not be read as licence to
drop the low-RAM rule:** it finished with an **8.72 GB footprint on an 8.0 GB machine** and 3,443 MB
of peak swap, 2.7x the low-RAM runs. It fit by paging, not by fitting, so
`Z_IMAGE_LOW_RAM_BELOW_BYTES` stays at 24 GB. Note the cost that remains: even low-RAM swaps
1.3-1.9 GB at this tier, at 5.6-6.5 minutes an image.

**Voice's deadline was raised to 1,800 s, because voice is the lane that actually failed.** Story Studio reported a **~17-minute narration part killed at the 900 s wall** — the concrete failure the flat constant caused. 1,800 rather than 1,200: 1,200 is three minutes of headroom over the part that died, and that part ran on one Mac, so the same narration on a slower node among the fleet's 35 eight-GB and 15 sixteen-GB machines would hit the identical wall at eighteen minutes. 1,800 is 1.76x the observed worst case and matches chat, so the two long-form lanes share one number. **Seventeen minutes is an observation of a failure, not a clean measurement, and 1.76x is inference** — the end state is to measure a real voice part and derive it, as Z-Image's 8 GB floor was derived from the owner's run.

**The fleet was wrong by a factor of seven.** It was recorded as 7 machines. The owner's own count is
**~53**: 35 x 8 GB, 15 x 16 GB, 2 x 24 GB, 1 x 32 GB — so **50 of ~53 are 16 GB or below**, which
inverts which tier a recommendation must be sized against. `AGENTS.md` also still called the 32 GB
machine an M1 Max; `research/owner-hardware.md` had corrected that to an **M2 Max Mac Studio
(`Mac14,13`)** on 2026-09-24, but the instructions file had not, and the fleet table guard reads the
research file rather than the instructions file, which is why it survived.

**Fixed on the way, all the same shape — a test or a document pinning a number.** One test asserted
`deadline_seconds == 900.0` literally; it now asserts the join, so it still fails if the Hub stops
publishing its own constant and no longer fails when the constant is deliberately changed. Eight chat
tests pinned `4096` or were hand-tuned to sit just above it — one carried the comment *"8192, not
4096: the request below asks for the worker's default output budget"* — and were restored to what
their fixtures were written to check rather than re-pinned around the new value. `docs/tasks/ST-20.md`
restated "the 900 s" against a line that no longer existed.

## 0.6.69 — 2026-09-26 · Voice's memory verdict never worked, and now it is shown

### The Audio page can tell you when a model is too big for this Mac

Every other page already did — Image, Music, Subtitle and Chat all warn you when a model asks for
more memory than the Mac has, and then let you try anyway. Audio was the one that said nothing.

Fixing that turned up why it *couldn't* have said anything: the part of the app that works out how
much memory your Mac has **had never worked**. It asked for `sysctl` by name, and on macOS that tool
isn't somewhere the app looks — so it got nothing back, quietly, and every Audio model has been
recorded as "memory: unknown" the whole time. It reads your Mac properly now.

So on the Audio page you'll see an orange note under the model when it wants more than this Mac has
— the same note the other pages show, in the same place. It's advice: you can still press Generate.

### Two smaller things

The web picker inside the app now finds every model's ready-made voices, not just one family's — so
a future voice model's voices will show up without anyone editing a list. And the app no longer
decides whether a cloning model needs a transcript from its family name; it reads what the model
itself says, which is what the two models that need one already publish.

### Technical notes

**SH-3, and it was worse than "no warning".** Voice was the one Studio that published no
below-tier sentence, so the Audio page had nothing to draw. Fixing that exposed the reason it could
not have drawn one anyway: `components/voice/app/backend/system_info.py` called a **bare `sysctl`**,
and `sysctl` lives in `/usr/sbin` — which is not on PATH for a packaged worker launched by the Hub
or from Finder, and is not on PATH in this project's own development shell either.
`_sysctl` swallowed the failure and returned None, so **`detect_memory_gb()` and `detect_chip()`
were both None and every model's `fit` verdict has read "unknown", fleet-wide, for as long as that
has been true.** The probe is looked up by absolute path now, and reports this Mac's 32 GB and its
M2 Max.

This is the defect the chat tier harness carries a warning about in its own header: a bare `sysctl`
that failed silently once made a swap sampler report a peak of 0 MB, which looked like a reading.

**The warning, wired end to end.** `catalog.serialize_model` publishes `memory_warning` under the
same key and in the same place as the other four Studios, derived from the `fit` verdict this worker
already computes — `risky` *is* "this host is below the model's floor", and its `hint` already names
both numbers, so there is no second threshold to disagree with the chip beside it. The native audio
model carries it, and the Audio page draws it with the same triangle the Image, Music, Subtitle and
Chat pages use. **Advice, never a refusal**: the model stays offered and `blockReason` remains the
only field that stops a job.

**Guarded from both ends.** `tests/test_below_tier_warning.py` now requires **five** publishing
workers and a render site for each — the expectation it was written to enforce, finally including
Voice. `components/voice/app/tests/test_system_info.py` pins the probe: it must read this machine,
it must work with `PATH` set to `/nonexistent`, and a host below the floor must be told with both
numbers while a host at the floor is not warned at all. Proved: reverting the probe to the bare
name fails two of the three.

**Also in this release.** SH-17's two consumers: the web picker now discovers every ready-made
roster by the *shape* of its key (`<family>_preset_speakers`) instead of naming one family's, and
the native app no longer decides whether a transcript is required from `family == "qwen3-tts"` —
the catalogue states that itself, and every shipped cloning model was checked before it was removed.

## 0.6.68 — 2026-09-26 · five releases shipped the wrong image engine

### The image engine has been wrong since 0.6.63, and your 8 GB run is what caught it

Z-Image was measured and approved on **mflux 0.20.0**. Releases 0.6.63 through 0.6.67 shipped
**mflux 0.17.5** instead — so every image timing you've been shown (Z-Image's ~166 s and ~236 s, the
memory peaks, the "measured on mflux 0.20.0" note) was describing an engine your app didn't have.

It happened because the release instructions pointed at the wrong folder — one that was correct when
they were written, and was never re-checked after a newer engine was built. I followed that line for
every release I cut. This build ships the right engine again.

Your 8 GB Z-Image run refused because of it, and the refusal was right. Its advice, though, was
wrong — it told you to install the kit's app because that carried 0.20.0, and the kit carried 0.17.5
too. So you'd have reinstalled and been refused again. That advice is now replaced with a command
that shows you what your app actually has, before the run decides anything.

**Nothing has to be re-measured.** The numbers were taken on 0.20.0 and this build now contains
0.20.0, so they describe what you're running again.

### The 8 GB Z-Image run is one double-click now

It was a `.sh`, which Finder opens in a text editor — the same thing that happened with the chat
tier runner. There's now **`RUN-8GB-Z-IMAGE.command`** on every drive beside it. Double-click that
one.

### The Technical folder on your drives has been tidied

You were right that it was a mess: **29 files where 9 belong**, including three that were exact
duplicates of each other under different names (two copies of the Z-Image runner, two of its
readme, two of the launch checker). They're leftovers from older kit layouts that nothing wrote and
nothing cleaned up.

They've been moved — not deleted — to a `_trash` folder on each drive, and the kit now maintains
that folder itself, so it can't fill up again. What's left in `Technical/` is the nine files the kit
actually uses.

### Technical notes

**The Image runtime regressed in 0.6.63 and nobody noticed for five releases.** 0.6.59–0.6.62
shipped **mflux 0.20.0**; 0.6.63–0.6.67 shipped **mflux 0.17.5**. Every Image number this product
publishes — Z-Image-Turbo 6B MLX 4-bit's 166 s and 236 s, its peak figures, its
`measured_on: "mflux 0.20.0"` — described an engine the app did not contain.

**How it happened.** The release loop in `docs/tasks/HANDOFF.md` named
`--image-runtime .build/st03-image-runtime-worker`. That directory is the **superseded 0.17.5** tree,
preserved as the receipt for the ST-03 packed-app proof — and it was correct when the line was
written, before mflux 0.20.0 existed as a separate built runtime. The 0.20.0 tree has been sitting at
`.build/image-runtime-mflux020` ever since, and every release since 0.6.63 staged the wrong one by
following a stale instruction faithfully. **That is the failure mode this project keeps recording: a
document that was true when written and was never re-checked against the artefact.**

**How it was found.** The owner ran the 8 GB Z-Image measurement on his 8 GB Mac. The harness checks
the engine version before it measures, refused, and named the mismatch — five releases after the
mistake. Its own advice (*"install the kit's app, it carries mflux 0.20.0"*) was **also false**: the
kit carried 0.17.5 too, so following it would have reinstalled and been refused again.

**The fix is the runtime, not the record.** `.build/image-runtime-mflux020` is byte-for-byte the tree
0.6.62 shipped — verified by content digest, `c3acf766…`, which is the same digest the 0.6.59 handoff
records as replacing 0.17.5's. Staging it restores the engine Z-Image-Turbo was qualified on, so its
published numbers describe the app again.

**And it cannot recur.** `packaging/stage_voice.py` now pins the qualified Image runtime by content
digest (`IMAGE_RUNTIME_SHA256`) and **refuses to stage anything else**, naming both digests, where
the handed tree came from, and the path to use. A version string would not have caught this: both
trees contain an `mflux` package and accept the same commands. This is the same mechanism that
already pins the Music overlay.

**Also in this release.** `RUN-8GB-Z-IMAGE.command` — the 8 GB Z-Image runner had no double-clickable
wrapper, which is the same omission that was fixed for the chat tier runner and not for this one; the
wrapper checks the installed app's mflux version and says so *before* the run refuses. The harness's
false advice is replaced with a command the operator can run to see what their app carries. And
`prepare-kit.sh` now treats the kit's `Technical/` folder as a **managed set**: it writes its nine
files and moves anything else to the volume's `_trash`. The owner found 29 files there, including
three exact duplicates (`8gb-z-image-run.sh` beside `eight-gb-z-image-run.sh`, and
`check-everything.sh` beside `verify-launch.sh`) — leftovers from earlier kit layouts that nothing
wrote and nothing removed.

## 0.6.67 — 2026-09-26 · three published things that were not true

### A picture's timing was wrong, and so was the machine it was measured on

The Image page told you how long FLUX.2 Klein 4-bit takes: about **79 seconds**. That number was
invented — not deliberately, but it was sitting in the app with nothing behind it, and no record
anywhere had it.

The real run was found in your own files: **105.9 seconds**, and it turns out it was measured on a
**16 GB M4**, not on the big Studio. So the honest number is a bit slower than you were told, and
it's a better number — it's what the machine you'd actually generate on does, not the fastest Mac
you own.

### A limit nothing could see

Qwen3-TTS had a *"stop at N seconds"* limit that worked — it genuinely capped the audio — but it was
invisible: nothing published it, so nothing could find it. It's published now, for the Qwen models
that honour it. Nothing about how it behaves changed.

### Nothing you'd notice

Two housekeeping fixes: a rule about your per-model defaults now has a test instead of a comment, and
a test that wasn't actually running (the test runner silently skipped it) now runs. Neither changes
what you see.

### Technical notes

Three open items, each the same shape: something the product published or relied on with nothing
behind it. Each fix is small; each guard was proved by breaking the thing it guards.

**FLUX.2 Klein 4B MLX 4-bit published `79.34 s` for a scene it described exactly — and that number
was in no file in this repository.** The stored run of that same scene — same model, same pinned
revision, 1280x720, 4 steps, guidance 1.0, seed 42 — is **105.943 s**, recorded in
`qualification/st03-image-real-inference.md` and again as `worker_terminal.duration_seconds` in
`qualification/evidence/st03-image-real-inference.jsonl` (job `af4913b2`, 2026-09-15). The host was
wrong too, and the correction is better news: the row claimed a 32 GiB M2 Max, and the record says
**"Captured on Apple M4, 16 GiB RAM, macOS 26.6.2"** — a measurement on the tier this fleet actually
runs. `qualification.packaged_inference` also said `pending` while that completed packaged run sat in
the tree; it now names the run.

`tests/test_image_loading_mode.py::test_every_published_runtime_is_found_in_the_record_it_names`
opens the file each row names and fails if the figure is not in it. Proved: with the row set back to
`79.34`, it fails naming the file; with `105.94`, it passes. **A scene description is not a source.**

**`max_output_duration_s` was enforced by the worker and published by no family.** The shape, the
API field and its validator were all real, and the dispatcher genuinely turned it into Qwen's
audio-token ceiling — but no family named it, so **no lane contract offered a bound that was being
applied**, and a scheduler reading contracts could not discover it. The `qwen3-tts` family declares it
now, and it is the only family that honours it: every other family accepts the field and ignores it,
and a lane must not advertise a key it throws away.
`test_every_parameter_shape_reaches_a_published_contract` is a set difference rather than a list of
names, so nobody has to remember it when the next shape is added. Proved: removing the declaration
fails it, naming exactly `max_output_duration_s`.

**Voice's "the lane decides" exception now has a boundary, in tests.** `_job_parameters` leaves a
request field alone when the caller sent nothing *and* the value in force is the lane's own stated
default — the engine resolves that default itself, and writing it in would record an override nobody
chose. That reasoning holds only while the lane states the value, and the rule lived in a comment.
It is the **only** such exception in the fleet: Image and Music fill an omitted field in
`validate_request`, Subtitle in `with_model_defaults`, Chat in `_resolved_parameters`, all
unconditionally. `TestLaneDecidesBoundary` pins all four cases. Proved: widening the condition to
`if params[name] is None` — which would silently kill every optional parameter's stored default —
fails six tests.

**A note on the guard that did not run.** The first version of that test class was named
`LaneDecidesBoundaryTests`, and pytest only collects classes named `Test*`. The suite reported the
same count as before and looked green. A guard the runner does not collect is worse than no guard,
because it reads as covered; the class is renamed and the reason is in a comment beside it.

## 0.6.66 — 2026-09-25 · the tab was filtering the models that could reach the tab

### The tab was hiding the voices

You were right that it was dodgy, and it was worse than fiddly: **the two controls were reading each
other in a circle.** The voice tabs (Clone / Ready-Made / Design) were the tabs *your selected model*
supports, and the model list showed only models that support *the tab you are on*. So a model that
couldn't do the mode you wanted also hid every model that could.

That is why Khmer TTS was invisible. It was installed, it works, and there was no Ready-Made tab to
press and nothing on screen saying a preset voice existed on your own machine — because the model you
happened to have selected only knew how to clone.

**The tabs are now what your Mac can do**, across all its voice models. Installing a preset model
brings the Ready-Made tab with it; a Mac that can only clone shows one tab, which is the truth.
Pressing a tab moves you to a model that can actually do it.

### Your page opened on a broken model

The Audio page was opening on **VoxCPM2 4-bit**, which is not installed on that Mac — its folder is
gone while its install record still said it was there. That's why Generate was greyed out with an
error under the model before you touched anything. It now opens on a model that can actually run.

If you deliberately pick a model you're about to download, it stays picked — a refresh won't take it
away from you.

### `[Errno 2] No such file or directory: 'voxcpm2-4bit'`

That string was shown to you twice on that page, and it should never have reached a screen. When a
model simply isn't installed, the app was reporting it as *damaged* and passing Python's own error
text straight through. Six of your models were labelled that way.

A model that isn't there is now just **not installed**, with an instruction you can act on —
download it in Models, or copy its folder in. (It also used to say "press Verify Files", which
can't conjure files that were never downloaded.)

### Ready-made voices can have a default now

Last release made the Models tab's defaults actually reach the engine for pace and word timings.
The **voice** had the same problem — the page always sent a name, so a voice you set in Models was
overridden — and it's fixed the same way. The voice picker now has a **Model default** row at the
top: leave it there and the model uses the voice you stored; pick one and that's what you hear.

### Technical notes

The owner, on the Audio page: *"i can't see a 'preset model' like the khmer tts if i'm on the clone
which is the default, i need to first switch the model to not selecting anything before i can switch
to voice design or ready-made or sth… i'm not sure what the actual bug here is but it's dodgy to
me."* It was a closed loop between two controls, and it hid every preset voice on his machine.

**The loop.** The voice tabs were exactly the **selected model's** modes
(`voiceModeTabs` read `model.voiceModes`), and the model picker listed only the models that
published **the current tab's** mode (`audioModels(for:)`). So the tab decided which models you
could see, and the selected model decided which tabs existed. With a cloning-capable model selected
there was no Ready-Made tab — and therefore no way to see that a preset model existed, including
Khmer TTS, which was installed. The only escape was to pick some other model that happened to
publish preset, which is exactly the fiddling he described. Repairing his selection was not a fix;
the two controls had to stop reading each other.

**The tabs are now the machine's modes.** `audioModes` is the union of what this Mac's Audio models
publish, taken over models that can actually run here — so a tab is never offered with nothing
behind it, installing a preset model brings the tab with it, and a Mac whose models only clone still
shows one tab. Clicking a tab moves the model to one that publishes it, chosen by
`preferredAudioModel` (best that runs here) rather than "first in the list", which could be a model
this Mac cannot generate with.

**The page no longer opens on a model that cannot run.** `reconcileSelections` accepted `supported`,
which is only *"this Hub ships a package for it"* — every TTS entry in the catalogue answers true.
His page opened on `voxcpm2-4bit`, **whose folder is gone while its install receipt still said it
was installed**, so nothing moved the selection and the page sat disabled with an error under the
model. Availability is now read, on a **transition**: a model that was runnable and stops being so
is moved off, and one already known unavailable is left alone — selecting a model in order to
install it must not be undone by the next poll.

**`[Errno 2] No such file or directory: 'voxcpm2-4bit'` was being shown to the operator, twice.**
`preflight()`'s catch-all in `components/model_packages.py` turned every `OSError` into
`state: invalid` with `str(exception)` as the reason, and the Hub republishes a package's `reason`
verbatim — the app draws it under the script and again under the model. Six of his packages carried
it. Two things were wrong: a package with **nothing on disk is `not_installed`**, not `invalid`
(`verify_installed()` in the same file already said so, with a sentence naming the paths it looked
for), and an errno with a relative filename is not something a person can act on. `preflight` now
separates absent from wrong, shares the one sentence `verify_installed` uses, and never publishes a
raw exception string. **A not-installed package also no longer tells you to "press Verify Files"** —
that cannot conjure files that were never downloaded; it says to download it or copy its folder in.

**SH-16 closed: `preset_speaker` is published by the lane that reads it.** It was accepted and read
by the Voice worker and sent by the app, and published by no family's contract, so a scheduler
reading only the contract could not learn how a speaking voice is chosen. The roster moved to one
home (`catalog.PresetRoster`, carried on the family) and the parameter is published for a model that
offers preset **and** has a roster — with the family's own ids as the enum. A single-voice family
(Khmer) and a cloning-only tier publish **nothing**, per the standing rule that a lane must not
advertise a key it discards. No shipped row qualifies today, because the roster's own model was
pruned in ST-13.B; a CustomVoice tier returning to the catalogue publishes it with no code change.

**And the app half of it, which is the same defect this release is about.**
`HubAudioParams.presetSpeaker` was a non-optional `String`, always encoded — `""` for a
single-voice model, otherwise a name **seeded from the roster**, so an untouched picker looked
untouched while the app made the choice. A voice stored for a model in the Models tab could
therefore never reach the engine. It is optional now and `audioSpeakerForRequest` sends only a voice
the operator actually chose from a roster this model publishes; selecting a model **clears** a voice
from another family rather than seeding a new one, and the picker's first row is the not-chosen
state, naming the stored voice where the producer states one and saying only that the model decides
where it does not. Three tests that pinned the old seeding are replaced by that invariant.

## 0.6.65 — 2026-09-25 · the default is a default, and the page control is the override

### The default is a default now

You said it plainly: set a default in **Models**, but the control on the page is yours and wins —
and anything you can change has to actually be given to you. That is now how every generator works,
and it fixed three things that were quietly ignoring you.

**Speed, on the Audio page.** You could set a speaking pace per model in Models and it did nothing
at all — the page was sending its own `1.0` on every job, so your value never left the app. Now the
slider sends nothing until you move it. Leave it alone and the model uses **your** default. Move it
and that is what you hear.

The slider's ends are no longer numbers written into the app: they are the model's own, so a model
with a narrower range gets a slider that matches it instead of one the engine silently clamps. And a
model with no pace setting now shows no slider, instead of one that did nothing. There is a
**Model default** button to put it back.

**Word timings, on the Subtitle page.** This one had no control anywhere in the app at all — it
stored `off` and sent `off` on every transcription, so a word-timings default set in Models could
never arrive, and you had no way to turn it on from the page either. There is now a **Word timings**
switch, and it only appears for models that actually produce them.

**The weight format line** used to guess from file names — a sharded model showed up as
"index, safetensors", which is not a format. It now shows what the package itself states, and lists
the weight files separately underneath, because which files were found is real information.

**One small thing you would have hit eventually:** the Audio page was sending a language code to
almost every voice model, including the Khmer VITS voices that take no language at all — 12 of your
13 voice families throw that field away. The box now only appears for a model that reads one
(Chatterbox), where it is required and says so.

### If you had set speed to exactly 1.0

Then it reads as "not chosen" now, so your Models default works. If you deliberately set 1.0
against a model whose default is something else, move the slider once to put it back.

### Technical notes

The owner's ruling, for every generator: *"we have a default but control on the page is
overwrite… we are only setting defaults for stuff we can. stuff that can be change or altered such
as speed must be given to the user."* A value set in **Models** is what a job uses. A control on a
generator page is the operator's own answer and always wins. And a control **nobody has touched
supplies nothing at all** — which is the part that was missing everywhere it mattered.

**Voice's pace.** The Audio page held `audioSpeed: Double = 1.0` and sent it on all four
generation paths. The Voice worker treats a supplied parameter as the caller's own answer — its
own words, and its own test: *"an explicit `word_timestamps=false` is still the caller's answer and
a stored `true` cannot override it"* — so a `speed` default set in the Models tab could never reach
the engine **from the app**. It now sends none until the operator moves the slider. Nil is encoded
away entirely (`encodeIfPresent`), so the request carries no `speed` key and the worker's own
resolution applies: the operator's stored pace over the pace the lane states.

**Nothing about the pace is fixed in the app any more.** `static let audioSpeedRange =
0.5...2.0` is gone. The bounds are the selected model's lane contract (`minimum`/`maximum`) and the
value shown is its own `default`, which the worker publishes **resolved** — it hands
`model_defaults.resolved`'s map to `lane_contract.for_model` before publishing it. The slider
labels follow the lane too, and a chosen value is pulled into this model's published bounds, which
are the same bounds the worker clamps to. A model that publishes no pace gets **no slider**, rather
than one whose movement changes nothing.

**Subtitle's word timings were worse: there was no control at all.** `wordTimestamps: Bool = false`
was persisted and submitted on every transcription request, and no screen in the app rendered it —
so a `word_timestamps` default set in Models was unreachable *and* the operator had no way to reach
the value either. It is now a toggle, offered only where the model's own runtime claims word
timings, showing the value in force, and the form adds the key only once the operator has answered
it. A model that produces no word timings gets no toggle.

**The weight format is the package's own statement.** The model detail line read
`readiness.files.weight_files` and joined the extensions off the file names, which is an
interpretation standing in for a fact: a sharded package read as the format `index, safetensors`,
and a package with no loadable weights had no answer at all. It now shows the row's own
`weight_format` and names that field as its source; the file receipt stays, as its own line, because
which files the runtime found is real data. The Subtitle worker had already published the stated
value on its availability row — the app was reading the receipt instead.

**A parameter the lane discards is no longer sent.** The Audio page sent `language` on every job,
and **12 of the 13 voice families publish no `language` parameter at all** — the lane omits it
precisely because it throws the key away (the Khmer VITS voices are Khmer by construction). The
field is now offered only where the lane publishes one, with the lane's own accepted codes beside
it, and Chatterbox — the one family that publishes it `required` — is refused clearly when the box
is empty rather than filled in with a code this app invented.

**Three tests were pinning the defect, and are replaced by the invariant.** They asserted
`var speed: Double`, that every audio path passed `speed: audioSpeed`, and that the slider used a
`0.5...2.0` literal declared in the app. All three were green for as long as the pace control
outranked the Models tab, and one of them *required* the hardcoded range — so nobody looked. The
rule now has a name and a test class (`GenerationDefaultOwnershipTests`): a generation control the
app owns must be **optional**, because "the operator has not chosen" is a state the request has to
be able to express, and a non-optional page value is a default the app invented.

**Migration.** Every build before this one wrote `audioSpeed: 1.0` whenever the slider had never
been touched, so a saved `1.0` is read as *not chosen* — otherwise the Models tab's pace would stay
dead on every Mac that has ever run the app. A deliberate `1.0` against a model whose default is
something else is the one value that cannot be told apart, and it costs one movement of the slider.

**Recorded, not fixed:** `preset_speaker` is accepted and read by the Voice worker and published by
**no** family's lane contract (SH-16), so a scheduler reading only the contract cannot learn how a
speaking voice is chosen. The app's behaviour is right — it offers only the roster the model itself
published — but publishing it means deciding what a contract-shaped enum for a per-model roster
looks like.

## 0.6.64 — 2026-09-25 · the fleet can reach itself

### Your Macs can reach each other again

None of your nodes could be reached by the controller. The joins had worked — the controller knew
all of them — but each Mac was answering only on an address it had years ago, or on nothing but
itself. **A Mac now answers on whatever networks it is actually on, every time it starts**: the
local one and the Tailscale one, added automatically. Nothing to tick, and nothing you set by hand
is ever removed.

This is the fix for the thing you kept having to remember. A new Mac always did the right thing;
the problem was that the moment *anything* was saved, that list was frozen — so a Mac set up
before it joined your tailnet stayed on a dead address forever.

You can also now see the truth from outside: every Mac reports **what it is actually listening
on**, so "bound to loopback only" is a fact you can read rather than a job that never arrives.

### One command to set a Mac up properly

If you would rather be explicit, or you are setting up from a script:

| Command | What it does |
|---|---|
| `--listen-auto` | Answer on this Mac's own networks, and keep doing it |
| `--listen-host 127.0.0.1` | Answer on exactly these addresses |
| `--fleet-mode` | The above, plus start at login **and start the Hub at login** — for a Mac that must be ready after a reboot without anyone opening it |

Each one is remembered, so it is a one-time step per Mac.

### The Models page, built around what each Studio actually does

**Models is now split by Studio** instead of one long list, because an LLM's context window, a
TTS model's word chunks, an image model's steps and ratios, and a music model's length are not the
same kind of setting. Search still works across all of them.

Each model now shows its own facts, and where each fact came from:

- **Chat** — the context window and the file that states it, and the output limit **marked as the
  worker's own** rather than the model's, because this checkpoint ships no file that states one.
- **Audio** — the **word chunks** you could not edit before: `section_max_characters`, its real
  range, the hard cap, the pause between sections, and how speed is delivered.
- **Image** — the size table with the actual pixels per ratio, which tier the model is qualified
  at, and whether it ignores guidance (Z-Image does).
- **Music** — the song lengths it accepts, with the one that renders in sections marked.
- **Subtitle** — languages, timestamps, weight format.

And **you can set your own defaults for each model** — steps and size for one image model, tokens
and temperature for one chat model, section size and pace for one voice model — **per model, not
per Studio**, because each model is weighted differently. A value you set is used when a request
does not name one, it is remembered per Mac, and "Restore This Model's Defaults" puts it back.
Nothing a Studio does not accept is offered as a control.

### Fixes you won't see

- **Chat's memory default** and its tokens-per-second readout landed in the last build; this one
  adds the per-model defaults behind them.
- **Two things that were described in prose are now facts**: an image model saying "I ignore this
  setting" publishes a flag rather than a sentence a program had to match, and Subtitle publishes
  its weight format instead of leaving the app to work it out from file names.
- **A promise we broke**: the previous handoff described this build's fixes as though the running
  app already had them. It didn't. There is now a rule against that — a document names the build
  that has the behaviour — and the handoff says so at the top.

### You can generate on a Mac again while it is a node

If you switched a Mac to **node** and then found you could not make anything on it, that was a real
blocker and it is fixed: the Hub refused **every** job you submitted from that Mac's own window,
because a rule meant to stop a *customer* owning a node's work could not tell the difference
between a customer and you sitting at the machine. Your own Mac now always runs your own work; work
arriving from a controller is refused exactly as before.

### Still yours to do

The **8 GB Z-Image-Turbo 6B MLX 4-bit run**: your manual test proved it works (~5 minutes, against
166–236 s on the 32 GB Mac). The script on the drive is what records the peak memory and swap that
decide whether 8 GB becomes a *proven* tier. It is in `Technical/8gb-z-image-run.sh`.

### Technical notes

Every node on the controller was unreachable. GatewayKH measured five of five answering
`ConnectTimeout`, and found the cause by checking the **installed bundle** rather than the
checkout: our 0.6.63 handoff documented this release's fixes as though the running build had
them, thirteen commits apart. So this one is the fixes, and the rule that stops the next
document from doing the same (`AGENTS.md`, "a document names the build that has the behaviour").

**A Mac now answers on its own networks, and keeps doing it.** First-run behaviour was always
right — every private and Tailscale address the Mac has — and it **froze the moment anything was
saved**. A Mac set up before it joined a tailnet stayed on a LAN address from that day forever,
which is exactly what 1101 and 1102 were advertising to their controller: addresses they left
months ago, while their tailnet addresses went unused. An absent `listenHostAutomatic` now means
automatic: recomputed on every launch as this Mac's current private and Tailscale addresses
**plus** whatever was already saved. **Additive — it can never remove a way in that worked.**

**The setter GatewayKH asked us to name exists and persists.** `--listen-auto` turns automatic on
and writes it down; `--listen-host <list>` pins an exact list and now also survives the next
launch (it used to last exactly until then, so a kit could configure a Mac and then watch it come
up on the old addresses with no way to tell "ignored" from "forgotten"); `--fleet-mode` is the one
switch for a Mac that must be ready after a reboot — listen + start at login + **start the Hub at
login**, the third being the one that mattered, since the app already started at login while the
Hub did not. It deliberately does not touch sleep, and says so.

**The bind is a fact, not an inference.** `/api/health` and `/api/hub/settings` publish what this
Hub is actually listening on: the hosts, their classes (`loopback` / `lan` / `tailnet` / `other`),
the port, and the two questions a caller asks — `loopback_only` and `reachable_off_this_mac`. It
is on `/api/health` rather than only on settings because settings is owner-only and a
**controller** is who needs to see that a node came up loopback-only instead of learning it from
a timeout. `known: false` is a different answer from loopback and carries its reason.

**Every Studio has per-model defaults, and the Models tab is rebuilt around what each lane
publishes.** Four of five lanes are here — chat, image, music, voice and subtitle — with the app
reading `lane_contract` for the first time (it read none of it before, which is why every fact the
owner asked for was already on the wire and not on screen):

- **Models is split by Studio** — one collapsible section per function, derived from the enum, so a
  new Studio gets a section with no edit in the app. Search flattens across them.
- **Each section shows that generator's facts with provenance**: the chat context window and
  where it came from, the max output labelled a worker default, TTS's section bounds and hard cap,
  the image size table with the tier it is qualified at, Music's duration ladder and single-pass
  cap, the subtitle weight format. A value nobody states renders nothing.
- **"Your defaults for this model"** is built only from the lane's own settable parameters, in the
  lane's order, with the bounds it enforces. A refusal's per-parameter reasons land under their own
  fields; a stored value the lane now refuses is shown with its reason rather than dropped.
- **The generator pages seed from the resolved values**, which matters because they send every
  field: a stored default the page cannot see is one the page overwrites.

**Two facts that were prose or a join, now published.** The Image lane says `"ignored": true`
rather than relying on a note's wording, and Subtitle publishes `weight_format` on the row the app
reads instead of leaving it to join file names. Both were found by the worker building the screen,
and both are the same shape — a consumer interpreting where a producer could state.

**And `lane_contract.structured_output`**, published by chat: how the JSON guarantee is kept
(`prompt_validate_repair`, `grammar_constrained: false`, one repair attempt), because "json_object
is supported" does not answer the question a strict-JSON caller has.

**And a Mac switched to a node can generate from its own window again.** It could not: one gate
refused *every* local submission in agent mode with a 409, on the sound rule that a node must not
own a **customer's** job — but the owner sitting at his own Mac is not a customer, and the gate
could not tell him apart from a remote controller. The difference is the credential and nothing
else: the Hub token is this Mac's own, the fleet token is someone else's. The fleet refusal is
pinned by the same test, because narrowing a gate is only safe if what it protected still holds.

### What is not here

Two app-side items are recorded rather than fixed: the Audio page's own persisted speed slider
still shadows a `speed` default set in Models (the page sends every field, so the Models value
never reaches the engine from the app), and the app still joins file names for the subtitle weight
format in one place. Both are in `docs/cross-project-open-items.md`.

## 0.6.63 — 2026-09-25 · Auto, and the controls that stopped lying

### Image gets the memory control you asked for

The Image page now has **Memory: Auto / Low RAM / Full**, and it is the real low-RAM lever rather
than a label. On **Auto**, each model decides from this Mac's memory — Z-Image-Turbo 6B MLX 4-bit
runs its bounded configuration below 24 GB and its full one at or above it. **Low RAM** forces the
bounded configuration on any model that has one, which is how you tell Image to stay small on the
32 GB Mac so Audio or Chat can keep a model loaded instead. **Full** forces the big configuration
back.

Each option says what it costs, in the model's own measured numbers: about **7.1 GB peak instead
of 17.7 GB**, and about **236 s instead of 166 s** for a 1280×720 nine-step image on the machine
it was measured on. FLUX.2 Klein 4B MLX 4-bit has no bounded mode at all — that is deliberate and
it says so on the page instead of showing you a control that would do nothing.

This is a **loading** setting and it lives on the Image page. The memory setting in Settings is a
different question — when a held model is let go — and Image's answer there is still the one
sentence it was.

### Auto — one memory setting that is right on every Mac

Every Studio's memory setting now has a fifth choice, **Auto**, and it is the new default:

- **16 GB and above:** hold the model for **10 idle minutes**, then release it.
- **8 GB:** release as soon as each job finishes.

That is the whole rule, and it means Chat and Music stop throwing their model away after every job
on the Macs that can afford to keep it — no more ~12-second reload before every Chat reply on your
16 GB and larger machines — while the two 8 GB Macs keep behaving exactly as they do today. The
setting tells you what it did: on Auto it reads *"Follows this Mac: releases after 10 idle
minutes here"*.

Nothing here can interrupt work. A held model is never released while a job is queued or running,
and if another Studio on that Mac needs the memory before it can start, the Hub already asks the
idle Studios to let go first — that is the *"unless another request is in"* part.

### Chat is now work you can see — and measure

**A Chat send is recorded as a job.** That means the Jobs page has a **Chat** tab listing every
question you asked and every answer, with the model, how long it took, and Copy Answer / Save….
Before this, a Chat send left no record anywhere.

**Chat rows show tokens per second.** You asked for a way to measure generation speed, and that
needs the model's own generation time rather than the whole round trip — a cold reply includes the
model load, so a rate taken from the round trip would have been wrong on exactly the turn you were
measuring. The worker now states its own generation time, and the row shows e.g.
`10 tokens · 1.4 tok/s · 7.2 s`. When the worker does not state a time, no rate is shown — there is
no invented number.

The Jobs page also has a **Video** tab now. It lists nothing and says so: Video is not in this
build yet, and an empty tab that explains itself is better than no tab at all.

Chat also ends with the same *"Model installation and storage are managed centrally"* line and
**Manage Chat Models** button the other four Studios have. And the message you send appears in the
transcript **immediately** now, instead of after the reply arrives — a slow reply used to look like
a dead button, and a failed one said nothing at all.

### 8 GB Chat now warns you, exactly like Image

On an 8 GB Mac, Chat tells you the model is qualified at 16 GB and that it will swap or fail,
especially with another Studio holding a model. It still lets you try — it is advice, not a wall,
the same as Image.

### Times always show their seconds

You asked for this and it was only half done. The run time was fixed last release; the line
directly under it was not. A 2 min 5 s clip read **"2.1 min"** on the row above **"2 min 5 s"**,
and a saved voice clip of 90 seconds read "1.5 min". Every duration in the app now goes through one
formatter that always states the seconds: `59.4 s`, `1 min 0 s`, `2 min 5 s`, `2 h 7 min 3 s`.

### Whisper small and base are actually usable now

They were listed as "catalog only" for the wrong reason. The Subtitle worker was supposed to reuse
the Voice Studio's snapshot builder — the one that can convert the `weights.npz` these two models
ship — and a one-word path error meant it never did in any installed build. The fallback kept the
catalogue looking healthy, so nobody could see it. Fixed and measured end to end: both models now
build a loadable folder, and a real transcription comes back correctly.

### The copy-models step now cleans up after a model is cut

`2 - Copy the models.command` still copies and updates exactly as before, and it is still safe to
run again on a Mac that is already set up. What is new is that it will also **offer to remove
models this kit no longer offers** — an old model you have stopped shipping just sits in
`~/StudioHubModels` forever otherwise. Nothing is deleted: they are **moved to
`~/StudioHubModels/_trash/`**, the list is shown before anything moves, and emptying that folder is
your call.

Two things it deliberately will not touch: the **Hugging Face cache** that lives inside the same
folder, and anything you put there yourself — including your **Yue2** download.

### Z-Image-Turbo 6B MLX 4-bit is now carried on 8 GB Macs

You asked for the override, so the copy step now carries it on 8 GB machines even though its
manifest's tier is 16 GB. It is recorded differently from FLUX — FLUX is in the "you have run it
there" list, Z-Image is in a new "you asked to try it there" list — because the 8 GB measurement is
still outstanding. The app still warns you it is below tier and still lets you run it, in low-RAM
mode.

### Fixes you won't see

- **A cancelled request no longer holds a Mac for six hours.** A cancel landed as an exception the
  proxy's cleanup didn't catch, so the machine stayed reserved. What a dropped connection does is
  now declared per route instead of guessed.
- **Chat's engine could wedge after a cancelled answer**, leaving every later question waiting
  behind it. Fixed, and measured: the next reply is served in about a tenth of a second.
- **A question too long for the model is refused, not silently truncated**, and the refusal tells
  you the measured sizes and the model's own limit.
- **Three imports in the Hub pointed at files that do not exist.** One of them made the cloud-model
  check report "nothing matched" when it had checked nothing at all.
- **Something now checks the bundle before it ships**: any module a worker imports that is not in
  the bundle stops the build. Run against the app you have installed today, it finds four.

### Still yours to do

The **8 GB Z-Image-Turbo test** is the one measurement outstanding, and it is now more useful than
ever: it decides whether that override becomes a proven tier. One command, instructions in
`qualification/README-8gb-z-image.md`.

### Technical notes

Four asks from the owner drove this one, and each turned out to be about the same thing: a
number or a control that was published somewhere other than where it was read.

**The Image low-RAM mode is now a control.** *"there is a real lever for image if you want one…
That could be exposed as Auto / Low memory / Full on the Image page."* mflux is built inside every
job and dropped when the job returns, so Image has exactly one *release* policy — that stays the
sentence it was. The new axis is how the model **loads**: `GET/PUT /api/loading-mode` with
`auto` | `low` | `full`, published per model as `loading_modes` on every catalog row and offered
on the Image page. Z-Image-Turbo 6B MLX 4-bit declares a bounded configuration (below 24 GB of
unified memory) and publishes three options whose sentences carry its own measurements: about
7.1 GB peak instead of 17.7 GB, about 236 s instead of 166 s at 1280×720, 9 steps. FLUX.2 Klein 4B
MLX 4-bit declares none **on purpose** — its own registry comment says adding a knob would be an
unverified change to a verified path — so it publishes `supported: false`, no options at all, and
its own sentence saying the machine-wide setting does not change how it loads. A control that
could do nothing is not offered.

It was called `memory-mode` for one afternoon and renamed before it shipped: the shared
`components/memory_modes.py` is the *release* table, a catalog row would have published
`memory_policy` and `memory_modes` side by side meaning different things, and "loading" is what
the setting actually selects. After a release that rename would have been a published contract.

**Every Studio gained `auto`, and it is the default.** All four workers shipped the same four
modes and all four defaulted to `immediate`. That default was measured — 2026-08-07, 16 of 19
fleet machines below the memory guard's 3.2 GB floor with swap burned, because each of 3–5
Studios on one 8 GB Mac independently believed holding its model was free — and it is the wrong
answer on a 16/24/32 GB Mac, where it buys a full model reload per job to free memory nobody was
short of. `auto` is right on both: **at 16 GiB and above it behaves as `balanced` (10 idle
minutes), below it as `immediate`**; a machine whose memory cannot be measured gets `immediate`,
never `balanced`. The rule is the one Subtitle already shipped, moved into
`components/memory_modes.py` so it exists once — every worker imports that table and asserts
identity, and the Hub derives its label-only table from it, so a sixth mode cannot exist in every
Studio and nowhere in the Hub.

The sentence the operator reads now comes from the worker, which is the only party that knows
what `auto` resolved to on that Mac: `options[].detail` says *"Follows this Mac: releases after 10
idle minutes here"*, and `status()` publishes `resolved_mode`, the resolved `idle_seconds` (never
null for auto) and `machine_mode`. A UI that re-derived it from a threshold could only guess.

Two packaging consequences, both proved by staging a real Voice-only bundle: Voice is the one
worker whose bundle did not put `Resources/` on `sys.path`, so `packaging/voice_worker.py`
appends it, and `stage_voice.py` now stages the shared `components/*.py` for **every** bundle
rather than only Hub ones. The Voice web UI also hardcoded its four radios and a "· default"
badge — Auto would have been invisible and unselectable there — and now renders the published
options.

**Chat became work you can see, and work you can measure.** A Chat-page send left no trace:
`GET /api/hub/jobs` is built from the ledger plus the broker's live queue, and the Chat route is a
synchronous proxy call that created no batch, so a Chat tab over that route could only ever have
been empty. `chat_jobs.record_page_completion` now writes one finished one-item batch through
`ledger.save_batch`, in the same shape every other studio's finished work takes, surviving a
restart; the answer is the item's `text` and the item is the artifact, because the worker streams
a completion and keeps no file. The other chat lane (GenStudio packs) has no producer and no
route on this Hub at all, so it is *declared* unlisted with its reason in
`broker.UNLISTED_JOB_LANES` rather than mapped into a second vocabulary — and
`tests/test_hub_jobs.py` now AST-discovers every backend module owning a job queue and fails,
both ways, if it neither reaches `listed_batches()` nor declares why not.

Tokens per second needed two facts and only one existed. `usage.completion_tokens` was on the
wire; the time was not usable, because the Hub kept only `runtime_s`, measured **around the whole
proxied call** — so the model load on a cold turn is inside it (the ST-14 review measured
6.4–8.8 s loads in its packaged probes; our own first packaged reply took 11.98 s end to end).
A rate derived from that would have understated the model's speed on exactly the turn being
measured, and meant something different on the next one. So the worker states its own generation
window on both paths (`studio_hub.elapsed_seconds`, measured in the engine and started where
`complete()` starts its own, after the model is resident), the Hub records it as `generation_s`
**beside** `runtime_s` rather than replacing it, and the rate is derived only where it is
displayed. A worker that states no time gets `null`, never a number derived from the round trip.

**And the Chat below-tier warning was already there.** The review had recorded *"the app ignores
the warning"*; measured, the worker publishes `memory_warning` derived from the model's own
manifest tier on an 8 GB host and nothing on a 32 GB one, and the Chat page draws it with the
same orange triangle Image uses. Nothing refuses, which is what the owner asked for. It is now a
test, and the test carries an invariant: no worker may publish a `memory_warning` that no page
renders. That invariant immediately found two gaps, recorded rather than papered over — Subtitle
computes a warning and publishes it at `readiness.host.memory_warning`, where nothing reads it
(and it cannot fire on this fleet, whose smallest Mac is exactly Subtitle's tier), and Voice
publishes none at all, only `min_free_memory_gb`, which is a *live-free admission floor* and a
different fact from a qualification tier.

**A published duration now states its seconds everywhere.** The run time was fixed in 0.6.62; the
lines beside it were not. `assetFacts` — the "Result details" line under every job row — still
rendered a clip's own length as `%.1f min`, so a 2 min 5 s clip read **"2.1 min" directly above
"2 min 5 s"**, and `NativeVoice.durationLabel` had the same copy, so a saved 90-second reference
clip read "1.5 min". All of them go through `NativeJob.clock` now. The transport label
("0:07 / 0:30") is a playhead, not a duration report, and keeps the shape every other player uses.

**The Subtitle worker now really does reuse the Voice snapshot-view builder.** The import was
`from components.voice.app.backend import cache`, and the bundle stages Voice at
`Resources/voice`, not under `Resources/components` — so it resolved in the checkout and nowhere
a worker runs. It sat inside a bare `except Exception`, so the local fallback kept the catalog
looking healthy while the one-source reuse the module docstring promises had silently stopped
happening in every shipped build. The fallback cannot convert `weights.npz`, which is what pinned
Whisper base and small ship, so those two were catalog-only **for the wrong reason**. Measured
after the fix, inside the bundle layout against the real library: `view_source=voice-cache` for
both, `weights.npz` converted, `runtime_supported: true` for whisper-small, and a real
transcription returns HTTP 200. Which builder produced a package's folder is published as
`readiness.files.view_source` rather than inferred.

**Two dead imports that could only ever raise.** `broker._monitor` and `cloud_guard` both fell
back to `from .main import monitor`, a module that has not existed since the Hub moved to
`standalone.py`; nothing took the path, which is exactly why it survived — `broker` raised
`ModuleNotFoundError` naming a file nobody could find, and `cloud_guard`'s was swallowed by a
bare `except` and looked exactly like a clean "no cloud model matched". `registry` imported a
module that is not in this project and never was. None is a behaviour change on the path anyone
takes; each now says what is wrong in the words of the thing that is wrong.

`packaging/stage_voice.py` gained `staged_import_gaps()`, which is why that class of bug stops
here: `copy_tracked_tree` copies what `git ls-files` lists, so a new module is invisible to
staging until it is added to the index and the bundle builds happily with a worker that dies at
import. Run against the bundle that shipped as 0.6.62 it reports four — these three, and the
Subtitle one — which is the release it would have blocked.

**A cancelled proxied request no longer pins a Mac for six hours.** Cancellation arrives as
`CancelledError`, a `BaseException`, so it slipped past both the transport handler and the
settlement after it, and the machine reservation survived until `_peer_lease_loop` expired it.
What a disconnect does is now declared per route, once: synchronous routes release, asynchronous
submits retain — and a cancel or ack POST no longer takes a machine lease of its own. Both
directions are pinned by tests that fail when the policy is inverted.

**Chat's worker got five fixes of the same shape** — a state the operator could reach but the
worker could not describe. A cancelled stream wedged the engine lock (Starlette never closes a
sync route generator, so `with self._lock` released only when the abandoned generator was
finalised, on whatever thread dropped it, and an `RLock` refuses a release from another thread;
measured ~0.11 s to the next served request after the fix). A prompt that could not fit the
model's stated context was silently truncated and is now refused before the SSE status line is
committed, naming the measured prompt tokens, the budget, the model's own limit and its source. A
boolean field accepted `"false"` as true. Sampling defaults lived as route literals and now live
beside the lane. `max_tokens` is validated against the model's stated limit instead of being
clamped without a word.

**The native Chat page joined the other four.** It ends with the same "managed centrally" note
and a **Manage Chat Models** row, pinned structurally for all five forms — the Manage row is the
last control, and its filter tag equals the Studio whose picker that page uses, parsed from the
enum rather than copied. The operator's own message appears in the transcript **before** the
request leaves, a failure marks that turn instead of inventing a model turn, a send while one is
in flight appends nothing, and `disconnect()` releases the busy flag. The Jobs page gained a
**Chat** tab with a real row (prompt, published model label including the quantization, state,
generation time, the answer, Copy Answer and Save…) and a **Video** placeholder tab that is
listed, files nothing, offers no controls or dates, and says what it will be.

**The kit's copy step now cleans up after a cut, and carries Z-Image on 8 GB.** The owner asked
for both: *"can you make the script prune out old models that's not supported or rejected? like
delete or move it to trash or sth?"* and *"as for the z-image, can you override it too so it can
run on 8gb machines too?"*

The prune's **safety property is the whole design**, because the obvious rule destroys data.
Measured against the real library, "any folder no shipped manifest names" would have moved two
things to `_trash`: `hub/`, which is the **Hugging Face cache** the Voice worker reads, and
`yue2-3b-mlx/`, the owner's Yue2 candidate, kept on purpose. So a managed package is a folder that
is **both** a key in the app's own `.studiohub/installed.json` **and** really a directory, and it is
pruned only when no manifest names it. The folder is tested second, never first — the index is a
historical receipt log (42 keys, 15 folders), so treating it as a picture of the disk would name
folders that are not there. It is a **move** into `<library>/_trash/<package_id>-<stamp>/`, shown
with its size before anything moves and covered by the same confirmation as the copy, including
when a prune is the only thing to do — which the old "nothing to copy" early return would have
skipped. Two corrections to my own numbers, both measured: the receipts this drops are **18**, not
the 27 I first said (9 of the 27 no-folder receipts are still shipped and correctly kept), and a
test for this script already existed (`tests/test_model_copy_script.py`), so the new file is
additional rather than a replacement. Dry run against the real library: **0 packages would move**,
18 receipts would be forgotten, and the four folders the app does not own are untouched.

Z-Image is carried on 8 GB Macs from a **separate table** from FLUX's. `PROVEN_SMALLER` means "the
owner has run it there"; Z-Image's 8 GB run is still unmeasured, so it goes in a new
`CARRY_BELOW_TIER` meaning "he asked to try it there". Writing it into `PROVEN_SMALLER` would have
made that table state something untrue, and `floor_origin()` names the exact table so a wrong skip
stays traceable. Measured: 8 GB machines copy **18 packages / 36.3 GB** instead of 17 / 30.4 GB;
16, 24 and 32 GB are byte-identical to before. The app still publishes Z-Image's below-tier
warning, so the operator is told what he is trading.

### The 166 s question

The image lane contract published **185.61 s** for Z-Image's default configuration. That number
entered the repo in the 0.6.61 registry commit and **nothing in the tree stores it** — no probe
file, no JSONL, nothing. The only record that does exist,
`qualification/z-image-turbo-2026-09-24.md`, says **166 s**, as does the constant's comment,
`README-8gb-z-image.md` ("166–236 s") and the 0.6.60 notes. Four places against one, and the one
had no artefact behind it — which is the thing this project refuses to publish. Both rows now
state 166 s and 236 s and name the document they came from, and the comment's "27 %" (the real
difference between the two configurations is 42 %) is fixed with them. FLUX's `79.34 s` also
traces to nothing; it is recorded as an open item rather than silently kept.

## 0.6.62 — 2026-09-25 · the Clone tab, and the end of guessing a voice's contract

### The Clone tab works the moment you open it

You told me the Clone tab showed no voice picker until you went to the Voices page and clicked "Use
voice". That was a real bug and this is the fix: your saved voices now load while the app is
connecting, so the picker is there when you get to it. If the list is ever slow or empty, the tab
now tells you which it is — reading, empty, or unreadable — instead of claiming the engine is
broken.

### One place to add a voice

Cloning now only works from a **saved voice**, which is what you asked for. The "choose a clip file"
picker is gone from the Audio page, along with "Prepare Voice" and "Transcribe Clip". To add a
voice, press **Add Voice…** on the Audio page — it takes you to the Voices page, which is where
voices are added and deleted. Then pick it on Audio and generate. Same on every model.

### The tabs tell the truth

Each voice model now says which kinds of voice work it can do, so Audio offers only those tabs. No
more picking "Design" on a model that can't design. VoxCPM2 offers Clone and Design, the Khmer
voices offer Preset, Pocket TTS offers Clone and Preset.

### Fixes you won't see but would have felt

- **Choosing a voice to clone from no longer refuses.** The last release made some models demand a
  formal audited profile; if your model didn't have one, the button just failed. Every model now
  takes its settings from its own published row instead — VoxCPM2 4-bit was being guessed at 24 kHz
  and its own row says 48 kHz — and anything genuinely unknown is named rather than invented.
- **Two runtime numbers are gone rather than wrong.** We were publishing a couple of generation
  times that only existed in a code comment. If we can't point at the measurement, we don't publish
  the number. The rest now come from stored records.
- **The Hub was mislabelling design support.** It decided by looking for "voicedesign" in the
  model's name, so VoxCPM2 and OmniVoice were advertised without Design, and Pocket TTS was
  advertised with a Preset mode it doesn't offer in that sense. Fixed at the source.
- **The voice test harness could never pass, and now can** — it waited for a startup message the
  engine doesn't print.

### Still yours to do

The **8 GB Z-Image-Turbo test** is ready: one command, instructions in
`qualification/README-8gb-z-image.md`. Send back the block it prints and the 8 GB tier stops being
a guess. It's the last unclaimed measurement in the Image work.

### Technical notes

The owner reported that the Audio page's Clone tab showed no voice picker until he went to the
Voices page and clicked "Use voice". Measured cause: the voice library was read by a task keyed on
**which tab was showing**, and changing tabs **cancelled** the read in flight. The store treated a
cancelled read as a failure, so it wiped the list and displayed *"the voice library is unavailable"*
— an error about the engine, when nothing was wrong with the engine.

**Half of that diagnosis was wrong, and the correction is the interesting part.** I assumed the
cancellation surfaced as a `CancellationError`; it does not. A cancelled `URLSession` read arrives
as `URLError -999` (`NSURLErrorCancelled`), and it was being wrapped into a transport error before
the store ever saw it — so a guard on the wrong error type would have fixed nothing. The library is
now read **as part of connecting**, once per connection, so no SwiftUI page task is on the critical
path at all; a cancelled load changes nothing; a real failure keeps the last known list instead of
destroying it; and the picker says which of *reading / empty / unreadable / no transcripts yet* is
true rather than asserting the most alarming one.

**Cloning is now library-only, by the owner's decision.** *"I don't want to just drop in any voice
and starts cloning no. I want to have a button to 'Add Voices' and bring to the voice tab to add
'voices' to the app. That way it can work across all."* The clip chooser, the reference-clip and
transcript blocks, `Transcribe Clip` and `Prepare Voice` are gone from the Audio page; the
saved-voice picker stays, with `Add Voice…` and `Manage Voices` leading to the Voices page, which
is the one place a voice is added or deleted. The `POST /api/generate/txt2speech/reference` route
remains for Gateway/GenStudio, which transports source bytes through it.

**The voice tabs now say what each model can actually do.** Every Voice model publishes which modes
it offers — `cloning`, `preset`, `design` — derived from its own capability row and its family's
facts, never from its name. The Audio page offers exactly those tabs, so a model that cannot design
a voice no longer offers a Design tab. A model that publishes none stays listed with no tabs and
generation blocked, naming what to update; there is deliberately no fallback, because a guessed
mode is the defect.

**The Hub was keeping a second, wrong copy of that answer.** `capabilities._voice_modes` decided
design support by checking whether the model's repo name contained `"voicedesign"` — the same
name-substring shape as the reference-audio bug fixed in 0.6.61 — and defaulted every other TTS
model to `preset_voice`. Measured against the shipped catalog it was **losing the Design mode** for
VoxCPM2 4-bit / 8-bit / bf16 (MLX) and OmniVoice bf16 (MLX bfloat16), and **inventing** a Preset
mode for Pocket TTS 4-bit (MLX 4-bit). It now reads the producer's field through one mapping table
and fails loudly on a mode it does not know, naming both files to update. Verified on a staged
bundle: all **13** voice rows serve their modes and the Hub maps every one.

**A9 corrected: the reference contract is derived, not demanded.** The first fix for that row
replaced a name-substring guess with "a cloning model must have an audited profile", which traded a
wrong result for **no result** — every un-audited cloning model refused on the upload route. The
contract now comes from what the model publishes: its own `sample_rate_hz`, its own transcript rule
from its published guardrails or its family's parameters, marked `profile_audited: false` with a
source for every value, and a bound nothing publishes recorded as *not published* rather than
defaulted. VoxCPM2 4-bit (MLX) resolves **48 kHz** where the old guess was 24 kHz. A refusal now
means a catalogued cloning model published nothing derivable, and it arrives with the missing field
and both files to fix.

**Two of our own published numbers were deleted rather than kept.** The voice lane's runtimes were a
hand-kept table whose values existed only as prose in comments. They now come from stored
measurement records, each row naming the probe it came from — and where no record existed the number
is **gone**: Pocket TTS 4-bit (MLX 4-bit)'s 0.30 s and VoxCPM2 4-bit (MLX 4-bit)'s 61.9 s / 738.4 s
are recorded as withheld with their reasons. Same rule we hold the models to.

**The smoke pilot could never have worked, and running it is how that was found.**
`smoke_voice.py` waited for uvicorn's `Uvicorn running on …` line, which uvicorn does not print when
the worker supplies its own sockets — so it could never see a worker that had in fact started, and
reported a startup failure that was not one. It waits on the worker's ready file now and reaches
`/api/catalog` in about two seconds. The pilot records a shipped model, **Qwen3-TTS-12Hz-0.6B-Base-8bit
(MLX 8-bit)**, with the tracked reference clip and its transcript, and the parameters were proven
through the shipped bundle: `done`, a 230,444-byte 24 kHz WAV, 24.26 s.

**A voice-only bundle can now resolve its own facts.** `_stage_voice` staged no `model-manifests/`
— only the Hub stage did — so a voice-only artifact, which is what the smoke pilot builds, had none:
a clone failed `QWEN_VALIDATOR_MODEL_UNDECLARED` after its reference had prepared fine, because the
clone-quality validator reads the model's `requires_packages` from its manifest, and the ASR leg
could not resolve its Whisper processor. Proved **inert for a shipped bundle** by controlled
experiment on one tree: staging with and without the added line gives a byte-identical
`payload_sha256`, `runtime_sha256` and every component's `source_sha256`.

**The 8 GB Z-Image-Turbo 6B MLX 4-bit run is now one command.** `qualification/eight-gb-z-image-run.sh`
was rewritten so it can be handed to an 8 GB Mac and run unedited, and
`qualification/README-8gb-z-image.md` is the instruction. Seven harness bugs were found by testing
it, each of which would have produced a *wrong answer* rather than no answer — a watchdog that killed
only the timer and left the model running, a stale image from an earlier run that could read as OK,
a refused run that still created its output folder, a silently truncated results file, a hardcoded
step count, a version comparison that warned about a correct engine, and a verdict that could label
an all-OK run "NOT QUALIFIED". A run stopped by the swap or wall cap is `ABORTED` and yields
INCONCLUSIVE, never "not qualified". **No 8 GB figure is claimed** — that measurement is the owner's
to run.

**Verification.** `tests/` — **944 passed, 11 skipped**; `tests/hub_regression/cases` — **175
passed**; `components/voice/app/tests` — **724 passed, 19 skipped**; `bash native/build.sh` and
`tests/native/pool-probe.sh` green; and a staged bundle was launched to confirm the 13 voice rows
serve `modes` and the Hub maps them.

**Still open:** the 8 GB tier measurement; `channels` and reference min/max duration are published
nowhere, so an un-audited cloning model enforces no length bound (the 25 MB upload cap still applies);
an unexplained shell anomaly where the Audio page's outer `.task` chain never fires in a hosted
window (the fix no longer depends on it, but a future page should not assume it runs); and
`capture_model_manifests.py --write` still withholds two multi-source packages rather than
overwriting them.

## 0.6.61 — 2026-09-25 · no model needs a code change

### The models that were there all along

If you looked for Khmer TTS in the Audio picker and could not find it, the app was wrong, not your
Mac. Four voice families that ship in every kit — Khmer MMS, Khmer female ONNX, Chatterbox (4-bit
and 8-bit) and Fish Audio (8-bit and bf16) — were being filtered out of the picker by a list inside
the app. Six of the thirteen voice packages were invisible. They are all there now, and the same
check has been applied to every other studio, so nothing that ships can be hidden again.

### Every model you have is now a model you can pick

- **Transcription** no longer insists a model's engine be called "Whisper". Anything the worker
  offers and the kit carries is listed.
- **Image and Music** no longer need a model to publish exactly the controls the first model
  published. A model with its own set of controls appears with its own controls, and anything it
  does not support is simply not sent.
- **Chat** uses the same model picker as the other four studios, and the page no longer names one
  model in its message whatever you have selected.
- **Installers** now check that every runtime the release says it contains is really there, not
  just Voice's — so a half-copied update is refused instead of installed.

### Offline kits really are offline

Pocket TTS 4-bit loads its tokenizer and its built-in voices through a model cache. The kit copied
the package, but nothing put it where the engine looks, so on a Mac that had never downloaded that
model the first generation would have failed — the kit's whole purpose. Installing a model now also
places its companion files where the engine reads them. Verified with the network switched off.

### Smaller, true things

- **Builds are named.** Every studio reports the version it was installed from. Before, four of the
  five answered the word "standalone", so a Mac's build could not be told apart from another's.
- **A failure says what failed.** "Could not reach the node: " with nothing after the colon now
  names what it tried and why. A credential refusal says which credential it wanted and which one
  arrived.
- **Numbers are the model's own.** The Chat page was showing a context window of 1,000,000 tokens
  from a generic default. Qwen3.5-9B 4-bit's own configuration says 262,144, and that is what is
  shown now — along with where each number came from.
- **One licence line, no advice.** Model notes state the licence name where it is known and say
  nothing about what you may do with it.

### For anything you add later

Adding a model no longer needs a change to the app. The catalog and the manifests are the list;
every screen, router and worker reads them. Where something genuinely cannot be derived, it now
fails with the file to edit named in the message instead of quietly leaving the model out.

### Technical notes

The owner's rule, recorded in `AGENTS.md` on 2026-09-24: *"my rule is never hardcoded — everything
must be dynamic for all generators and models, because we always have a new model to add."* This
release is the audit that made it true rather than aspirational, and the work of 73 findings.

**The reported symptom was khmer-tts not appearing, and the cause was broader than it looked.**
`native/StudioAPI.swift` decided voice-model support from a hardcoded family allowlist
(`qwen3-tts`, `voxcpm*`, `omnivoice`), and the Audio picker lists only supported models — so four
**shipped** families were invisible: `khmer-tts-mms` (VITS), `khmer-tts-female-onnx` (ONNX),
`chatterbox-mlx` (4-bit and 8-bit) and `fish-audio-mlx` (8-bit and bf16) — **six of thirteen voice
packages**. The catalog, Hub, manifests, library and all four kits offered them correctly; the app
alone refused to list them. A test asserted the broken behaviour, so it was green by construction.

**Everything in the audit's Tier A (18 rows) and Tier B (40 rows) is fixed**, verified against the
tree rather than against the report — `research/hardcoded-model-lists-audit.md` now carries the
disposition. What that means per studio:

- **A new model is now data.** Families, engines and modalities are read from the producer's own
  registry and the manifest; where a list is genuinely unavoidable it fails loudly naming the
  missing entry and the file to update, the way the Hub's engine table already did.
- **Voice**: one wiring table instead of six (`_ENGINE_REQUIREMENTS`, `_WIRED_FAMILIES`,
  `_SECTIONED_FAMILIES`, four behaviour sets and 14 `long_form_policy` branches, nine of them for
  families cut in ST-13.B). The MLX label and the "🍎 MLX only" filter read the family's own
  `engine`, which is the bug that had already shipped once. Every cloning model is governed by its
  published capabilities instead of a name substring, and a Whisper tokenizer source is derived
  from the model's own manifest instead of defaulting to another model's vocabulary.
- **Hub and Chat**: `chat-mlx` is in the modality vocabulary that the fleet join, the worker table
  and the ledger all read. `chat` jobs are governed by the memory gate, appear in fleet activity,
  and the Chat worker publishes a real activity snapshot instead of a permanently empty one.
- **Subtitle**: the worker's catalog is *every manifest declaring its engine*, not the packages
  whose id starts with `whisper-`. Each publishes its own `family`, languages and timestamp
  support from the manifest rather than claiming Multilingual and word timestamps for everything.
  Its default model comes from `"default": true` in the manifest, with the rule published.
- **Native**: the Transcription picker no longer requires an engine whose name contains
  "whisper"; image and music configurations no longer require the shipped engine's exact control
  set (a second engine of either modality decoded to `nil` and vanished); the Chat page uses the
  same model picker as the other four; the four per-modality display lists are derived from
  `StudioFunction`; and the installer verifies every runtime the release manifest declares, not
  just Voice's.
- **Tooling**: the capture tool can reproduce all **24** shipped packages (it could rebuild 17),
  and refuses to overwrite one it cannot; the fleet measurement covers music and chat; the image
  runtime smoke imports every class the Image registry names, so a broken Z-Image-Turbo module
  fails the smoke instead of failing on first use.

**One companion relationship, one spelling.** A manifest declares what it cannot run without in
`requires_packages`; `dependencies` was the first spelling and was read by nothing that resolved
the relationship, so `chatterbox-4bit`/`chatterbox-8bit` (MLX 4-bit and 8-bit) shipped with their
`s3tokenizerv2` tokenizer **never verified or downloaded** — the engine fetched it at generation
time, which is the offline failure the declaration exists to prevent. `validate_manifest` now
refuses the legacy spelling by name, and the whole catalog is migrated. Measured: exactly those two
packages' verification receipts change, and they re-verify once; nothing else is affected.

**A companion package now reaches the path the engine loads from.** This was a real bug with a
measured proof: Pocket TTS 4-bit (MLX 4-bit) loads its tokenizer and preset voices by
`hf_hub_download` through `HF_HUB_CACHE=<library>/hub`, and an installed package lived only at
`<library>/<package_id>/<revision>/model/`. On a Mac whose cache had never seen the repo — a kit
node that copied packages and nothing else — generation failed offline. Installation now mirrors
each companion into the HF cache layout from the manifest's own `source.layout` and
`files[].source_path` (no repo, family or filename list anywhere), hard-linking where it can,
idempotent, and degrading with a named reason on a read-only library. Verified with the network
off: the engine's exact call opens the mirrored `tokenizer.model`, sha256 `d461765a…`, and Pocket
TTS 4-bit generated 3.36 s of audio, byte-identical across runs.

**Published facts now carry their provenance.** Gateway Studio measured this from the consuming
side: with nothing stated it fell back to a curated flat `context_window_tokens = 1,000,000` and
`max_output_tokens = 128,000`, labelled *curated*, which a UI then showed as the model's own. The
shipped **Qwen3.5-9B 4-bit** states `text_config.max_position_embeddings = 262144` in its own
`config.json`; that is now what is published, with its source, and `max_output_tokens: 4096` is
labelled as the worker's default because the checkpoint ships no `generation_config.json`. Where
nothing states a value, nothing is published. The manifest's dead `parameters_source` pointer at a
file neither the package nor the publisher ships is fixed.

**Every lane publishes how to ask it for work.** `docs/lane-execution-contract.md` specifies
`lane_contract`: the canonical operation, the parameters with the bounds that are enforced, the
artifact form, the terminal vocabulary with `uncertain` marked **not terminal**, and what a cancel
does not promise — built from each model's own limits, beside the validator that enforces them.
Image, Music, Voice, Transcription and Chat all publish it, and `/api/hub/model-packages` now
carries `modality` and the contract, so a scheduler needs one read instead of a best-effort second
one joined on model id.

**A version field carries a version.** Four of five workers answered the literal string
`"standalone"` on `/api/health`, which the Hub republished as `workers[].studio_version` — a value
that reads as data, is indistinguishable from a version that failed to load, and makes every Mac
look like the same build. Every worker now reports the release it was staged from, through one
shared reader, and the Hub refuses to republish a value that cannot be a version.

**A message that stops at a colon is worse than no message.** A node detail read
`could not reach the node: ` with nothing after it. Refusals now name the peer, the exception type
and the reason; a fleet-credential refusal names the credential it required and the one it was
offered; and an unknown engine names the engine and both files to register it in.

**The owner-approved production floor moved to the model that has it.** The 3.2 GB live-free floor
for Qwen3-TTS 12Hz 0.6B Base 8-bit (the 8 GB voice-cloning tier, measured cold requirement 3.15 GB)
lived in the Hub, keyed by that exact repo id, because the Voice worker published no free floor. A
model-keyed table in a consumer is the defect this release is about — the next model the owner
proves would silently get nothing until somebody remembered to edit it — so the floor is now on the
model's own catalog row (`min_free_memory_gb`) and the Hub's table is deleted. Two guards replace
it: `memory_admission`'s reason now distinguishes "its worker publishes a 3.20 GB live-free floor
and that is what is enforced" from "nothing was published, the generic 2.0 GB default applies"
(it previously said the generic default applied *while enforcing 3.2*), and a test reads the
shipped Voice row end to end so a row that loses the field fails rather than quietly relaxing.

Also in this release: the two documents a reader is most likely to trust state numbers that a test
now **recomputes** rather than a human maintains (the catalog is **24** packages /
**70,529,384,759** bytes, and six engines ship); `tests/hub_regression`, the adapted-upstream parity
suite, is in the verification command set instead of being hidden behind its own `collect_ignore`;
and advisory commercial-licensing sentences are gone from shipped, user-visible copy, per the
owner's 2026-09-22 rule — a licence is now a name and a source, stated once as a fact.

**Verification.** `tests/` — **935 passed, 11 skipped**; `tests/hub_regression/cases` — **172
passed**; `bash native/build.sh` and `tests/native/pool-probe.sh` green.

**Open, and named rather than guessed:** Pocket TTS 4-bit (MLX 4-bit) ships eight preset voices
(the `pocket-tts-preset-voices` package) and also declares cloning, and a model that offers both
is reachable in only one tab today; the producer field to close it is a per-model `preset_speakers`
roster derived from that package plus a published `modes` list. The audited reference-audio
profiles exist only for the Qwen families, so private-upload reference preparation now refuses by
name for the other cloning models instead of using a guessed copy — closing that needs measured
profile records, not code. And the recorded voice smoke pilot names
`Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit`, which the catalog cut in ST-13.B, so the smoke reports
that role as unexercised until a shipped model's parameters are recorded.

## 0.6.60 — 2026-09-24 · Z-Image-Turbo 4-bit can be tried on an 8 GB Mac

### Z-Image-Turbo on an 8 GB Mac

A fix so the 8 GB Macs can actually try Z-Image-Turbo 4-bit. When it shipped in 0.6.59 it asked
for 8 GB of *free* memory before starting — which an 8 GB Mac can never have, so the job would
have queued forever instead of running. It now asks for 3 GB free, which is a guard against
starting into a nearly-full Mac rather than a disguised "this machine is too small".

8 GB Macs are told the model is above their tier, and it runs in its low-memory mode. Whether it
belongs there is being measured; this release is what makes the measurement possible.

### Technical notes

A one-line fix with an outsized effect, and it was mine. When Z-Image-Turbo 4-bit was added it
published a **live-free-memory floor of 8.0 GB**, taken from its measured low-RAM footprint
(7.12-7.24 GB on a 32 GiB M2 Max). That conflated two different things: a *footprint* is not a
*free-memory* requirement.

On an 8 GB Mac a floor of 8 GB can never be met — and the Hub answers an unmet free floor with
**"wait"**, not "skip" (`broker._memory_gate`), so the job would have sat waiting for memory that
was never going to arrive. The one tier with no measurement was the one tier that could not run to
produce it.

- The floor is now **3.0 GB**: a real guard against starting into a nearly-full Mac, a little above
  FLUX's 2.0 because Z-Image's weights are 28 % larger, and satisfiable on an 8 GB Mac.
- Verified against the gate itself: an 8 GB Mac with 3.0-5.0 GB free now returns `run`; with 2.0 GB
  free it correctly returns `wait`.
- The test now asserts the **invariant** rather than the number — the free floor must stay below the
  smallest fleet machine's total RAM, or it silently becomes a tier in disguise and stalls instead
  of refusing. The old test pinned 8.0 and passed while the bug was live.

What decides "this machine class is unqualified" remains the published **tier**
(`min_unified_memory_gb = 16`), reported with a below-tier warning and deliberately never a wall —
per `memory_admission`'s own doctrine that a RAM floor is a qualification tier, not a wall. So an
8 GB Mac is offered Z-Image-Turbo 4-bit, warned that it is below tier, and runs it in low-RAM mode.
The 8 GB measurement is what turns that into a qualified tier or a documented exclusion.

## 0.6.59 — 2026-09-24 · Z-Image-Turbo, and the engine it needed

### A second image model, and it spells properly

**Z-Image-Turbo** is now in the Image model list beside FLUX.2 Klein. The reason it is there is
text: asked for the title "THE GOLDEN LIGHT OF DREAMS", FLUX writes **"THE GOLEN LIGHT OF
DREAMS"** — and at more steps it gets worse, adding an extra E to DREAMS. Z-Image gets all five
words right.

It is the slower of the two — roughly 3 minutes an image against FLUX's 80 seconds — and it is
much easier on the machine, using about a third of the memory. On a 16 GB Mac the app
automatically runs it in its low-memory mode, which is the configuration that has been measured
to fit.

### What to expect

- Z-Image appears in the Image model list and can be chosen like any other model. Nothing else
  about the Image page changes.
- It runs in a lower-memory mode on smaller Macs automatically. That mode is slower; the speed
  difference is the price of fitting.
- **The 8 GB Macs are not yet measured for this model.** It may work there and it may not — the
  app does not claim either way, and it will tell you if a Mac is below the tier this model has
  been qualified on.

### FLUX.2 Klein under the hood

The image engine was updated to run Z-Image, and FLUX.2 Klein was re-checked on the new version
rather than assumed to be fine. Its pictures are marginally different in the way any engine
update changes them — same scene, same composition, same text — and nothing about how you use it
has changed.

### Technical notes

Image gains a second model. **Z-Image-Turbo** is selectable beside FLUX.2 Klein, and it renders
text FLUX gets wrong — the title "THE GOLDEN LIGHT OF DREAMS" comes out correct where FLUX writes
"THE GOLEN LIGHT OF DREAMS" at 4 steps and worsens at 9.

**The engine moved for it.** mflux 0.17.5 → **0.20.0**, mlx 0.31.2 → **0.32.2** — 0.17.5 pins
`mlx<0.32.0` and 0.20.0 requires `>=0.32.0`, so the two had to move together. The re-lock held
every other package: **75 → 63 packages**, of which 12 are the `twine` subtree that mflux 0.20.0
no longer requires. Nothing unrelated moved, proved by a byte-identical second compile.

**FLUX.2 Klein is re-qualified, not assumed.** Its output pixels do change between engines —
94.2 % differ, SSIM 0.972 — but the distance sits on glyph outlines and sparkle edges with flat
interiors near zero. Visually the same picture: on the typography scene the title is identical in
wording, typeface, size and line breaks, including the pre-existing "GOLEN" typo, which the bump
neither fixed nor worsened. It cannot be attributed to mflux alone: mflux and MLX moved together.

- The Image worker ran exactly one model — repo, revision, class import and defaults were module
  constants. It is now a registry, and the catalog advertises both models with their own steps,
  limits, memory policy and qualification block. FLUX's row is unchanged.
- **Z-Image's memory is chosen from the Mac, not fixed.** `low_ram` is not a constructor
  parameter, so the plan is applied through `CallbackManager._register_memory_saver` — the CLI's
  own mapping — and raises if mflux ever renames it rather than silently not applying. Below the
  tier the default was measured on, the worker takes the configuration with the known bound
  (7.1-7.2 GB measured) and pays the measured time cost.
- **It does not claim the default fails on 16 GB.** That figure is cache-inflated from a 32 GB
  host, and the same reasoning would wrongly rule out FLUX, which ships on 16 GB. The default's
  requirement there is unmeasured and said so.
- **The 8 GB tier is unclaimed** pending the owner's measurement. Two named constants are the
  only knobs to move when it returns.
- Z-Image publishes a live-free-memory floor of 8 GB. The Hub enforces only that floor and
  defaults to 2 GB, which would dispatch onto a Mac with 3 GB free and let it thrash rather than
  refusing. FLUX keeps its existing behaviour deliberately.
- `validate_request` now rejects non-finite guidance — the same NaN-acceptance class the
  independent review found in the chat worker.

Evidence: 34 image tests, native boundary/API/lifecycle suites, and a live catalog read showing
both models `execution_ready`. FLUX was run through both the legacy path and the new registry and
all three outputs are pixel-identical (`16a5535f…`), a value that independently reproduces the
0.20.0 re-qualification record.

## 0.6.58 — 2026-09-24 · hotfix: 0.6.57 left every model picker empty

### Do not install 0.6.57

0.6.57 broke the app: every model list came up empty and the Hub showed as not answering. The
cause was a single missing entry in a lookup table — the new Chat model was added to the list of
models the app offers, but not to the table that says which worker runs it. Asking the Hub for the
model list then failed outright, and the app treats a failed model list as "the Hub is down".

Nothing was wrong with your Mac, your models or your network, and updating fixes it. If you
already installed 0.6.57, the app would have looked broken the moment it opened.

### Technical notes

**0.6.57 shipped a broken build and this fixes it.** Do not install 0.6.57.

The Chat manifest reached the release catalog in 0.6.57, but the Hub's engine→worker map in
`components/hub/standalone.py` was never extended for it. That map was an inline dict lookup, so
`GET /api/hub/model-packages` raised `KeyError: 'chat-mlx'` and answered **HTTP 500** — on every
Mac, because the manifest ships in the catalog whether or not the model is installed. The native
app reads that route with a hard `try` inside `refresh()`, so the 500 did not degrade one list: it
threw the whole refresh, set `isReachable = false`, and left **every model picker empty with the
Hub reported as not answering**.

- The mapping is now the named `_worker_for_engine()`, with `chat-mlx` added, and an unknown
  engine raises a `ValueError` naming the engine and both files to update instead of a bare
  `KeyError` from inside a route.
- `tests/test_model_manifest_catalog.py` gains a guard that walks the **real** catalog and asserts
  every shipped manifest's engine maps to a worker. Every previous test of that route used fixture
  manifests, which is exactly why none of them caught this. Reverting the one-line mapping makes
  the new test fail, which is how it was checked.

Measured before the fix: `/api/hub/model-packages` → HTTP 500 with the real catalog in a live Hub.
After: HTTP 200, 20 packages, all six engines present, the chat package carrying
`worker_id: "chat"`.

## 0.6.57 — 2026-09-24 · a node and its controller can both see the group

### You can see your pool of Macs, from either end

Connecting a node and then looking for it did not tell you much: the Mac you had just connected
showed as "has not reported in yet" for a few seconds, and the node itself showed nothing about
the group it had just joined — no controller, no other Macs, and a line saying no Mac had joined
*it*, which is not the question a node is asking.

Now both ends show the whole group.

- **On the controller**, every Mac that has joined is listed the moment it joins, whether it is
  awake or asleep. A Mac that is switched off stays in the list, marked as off with the reason,
  instead of quietly disappearing — it is still part of your group.
- **On a node**, Fleet overview lists the controller and every other Mac in the group beside it.
  If the controller is not answering, it says so rather than showing you an empty group.
- **Each row says what the Mac is** — Controller or Node — and where it answers.
- **A node no longer offers "Send here".** A node does not choose where work runs; its controller
  does. The button is still on a controller and on a Mac running on its own.

Joining also records which controller you joined, on the Mac itself rather than only in the app's
settings, so a node can still name its group after a reinstall.

### Technical notes

Owner report: after connecting nodes, a Mac did not show the pool it belonged to, and the
controller did not show the pool of connected nodes either. Both were true, and both came from
the same gap — **the group was not a fact either end held.** The controller's Fleet overview was
built from whatever was in its dispatch registry at that instant, and a node held a borrowed
credential with no record of whose it was, so it could not name its own group at all.

- **Adopting a Fleet token now records which controller it came from.** `POST
  /api/hub/fleet/adopt` accepts an optional `controller_url`; the app sends the controller's
  literal private address (resolving a name first, since the Hub stores an address it can dial).
  This is the root fix: the address previously lived only in the app's preferences file, so a
  reinstall or a second window left the Hub holding a borrowed token with no idea whose. An
  adoption that does not name a controller **clears** the recorded one — a stale address shows the
  wrong Mac as the head of the group.
- **`parent_controller_url` survives an unrelated save.** It was cleared whenever the role was not
  `"agent"`, so a Mac that had joined but had not yet been switched to Node lost the record on the
  next rename or listen-address change. It now lasts exactly as long as the adopted credential.
  Promotion to controller still mints a fresh token and clears it.
- **The Fleet overview is built from the group, not the dispatch registry.** A freshly joined node
  read as *"this Mac has not reported in yet"* for the four seconds until the peer poll caught up,
  and a node with no studio running was missing entirely. Roster members are now probed directly:
  a Mac that is off reads as off, with a reason, instead of as absent.
- **A node's overview shows the whole pool** — the controller and the Macs beside it — by reading
  the roster back from the controller it joined. An unreachable controller reports the group as
  *unreadable* rather than showing an empty one.
- **UI:** rows carry a Controller/Node badge and their address; the header names the group being
  shown and says when the list is incomplete; a node's "Joined nodes" section names its group
  instead of claiming the Mac is alone, and no longer offers "Send here" — a node does not choose
  where work runs.
- `tests/test_voice_bundle.py` staging fixture gained the Chat component, which it predated: the
  fixture's file list was missing `packaging/chat_worker.py`, so both staging tests failed on a
  `FileNotFoundError` rather than on anything they were written to check.

Evidence: [qualification/fleet-pool-2026-09-24.md](qualification/fleet-pool-2026-09-24.md) —
11/11 new tests driving two live Hubs, 126 passed across the fleet/settings/staging suites, native
boundary + API + lifecycle suites pass, and `tests/native/pool-probe.sh` reads the pool back
through the app's own workspace store from both ends.

## 0.6.56 — 2026-09-24 · 8 GB Macs can generate images again

### Image generation works again on 8 GB Macs

An 8 GB Mac was showing that FLUX.2 Klein needs a 16 GB Mac and refusing to generate. The
model still runs there — the app was treating the model's *recommended* memory tier as a hard
requirement. It now warns instead of refusing, the same way it already did for every other
below-tier model. 16 GB, 24 GB and 32 GB Macs were never affected.

### The model is named in full again

The Image model reads **FLUX.2 Klein 4B MLX 4-bit** — so you can see the size and the
quantization at a glance in the model list, instead of just "FLUX.2 Klein".

### Technical notes

`7187d8d` (0.6.55) turned a model's *qualification tier* into a hard block, regressing
`f9feca5` ("a RAM floor is a qualification tier, not a wall") three days later. The Image
worker publishes `memory_warning` on any Mac below the 16 GB tier; the new `blockReason`
returned that warning as the model's reason, which set `runtimeReady` to false, and
`usableModel` then refused every submit. FLUX.2 Klein image generation was dead on every
8 GB Mac even though the worker itself would have accepted the job.

- **`blockReason` no longer returns `memoryWarning`.** The tier is still surfaced through
  `NativeModel.memoryWarning` and shown in the Image workspace; only the worker's own hard
  gate (`memoryEligible == false`) blocks. Voice's `memory_eligible` gate is unchanged.
- **Image label restored to `FLUX.2 Klein 4B MLX 4-bit`.** It had been reduced to
  `FLUX.2 Klein`, dropping the bits/weight/quantization the picker is designed to keep;
  `pickerName` still drops only the "recommended" and runtime-marker suffix.
- `tests/test_model_reasons.py` now pins the advisory-not-blocking behaviour instead of the
  regression.

Affected: every 8 GB Mac, Image (and Chat). 16/24/32 GB machines were never affected.
Evidence: native boundary, API and lifecycle suites pass; 14/14 Image tests pass.

## 0.6.55 — 2026-09-24 · two Khmer voices, and a manifest capture that no longer truncates

### Two Khmer voices, on your Mac

Voice Studio now has two Khmer text-to-speech voices, both working fully offline:

- **Khmer TTS (MMS VITS)** — a clear single speaker at 16 kHz.
- **Khmer TTS Female (VITS/ONNX)** — a female voice at 22.05 kHz.

Both are small (about 332 MB and 115 MB) and run on any Apple Silicon Mac, including the 8 GB
ones. They speak Khmer only, each has one built-in voice, and they do not clone a reference clip —
for cloning, keep using OmniVoice or VoxCPM2. Choose one, type your Khmer text and generate; long
scripts are split and joined for you.

Both voices are already in the model library on every kit drive, so there is nothing to download.

### Technical notes

Voice Studio gains two single-speaker Khmer families (ST-12, owner request). Every other TTS
family is mlx-audio, with F5-TTS the only PyTorch exception; neither new model is MLX, so each got
its own worker and dispatch branch under a new packaging engine, `voice-vits`.

- **`khmer-tts-mms`** — `khmerttsopensource/khmer-tts` @ `c9380a9…`, Meta's MMS Khmer VITS
  fine-tune: 83M parameters at 16 kHz through transformers `VitsModel` on CPU. No new Python
  dependency; `torch` and `transformers` were already pinned.
- **`khmer-tts-onnx`** — `sengtha/khmer-tts-female-v2` @ `c6fde511…`, a Coqui VITS female voice at
  22.05 kHz through ONNX Runtime on CPU (`khmer_tts_v3.onnx`). Adds `onnxruntime>=1.20` to the
  generation requirements; the 998 MB training checkpoint is excluded.
- Both are single-speaker and Khmer-only, and **refuse a reference voice** rather than ignoring it.
  Long scripts use the existing conservative sectioning, so no long-form-policy change was needed.
  Model-cache slots, eviction, memory preflight, readiness and diagnostics all cover the two
  families.
- **Manifest capture no longer silently truncates.** The Hub `/tree` API pages at 50 entries and
  the capture tool read a single page, so the MMS repository captured 2 of the 9 required files
  while the manifest looked complete. The tool now follows the `Link` header and supports a
  per-choice `exclude` filter. The manifest catalog grows to 37 packages (89,369,563,824 bytes).

Verified locally on a 16 GB M4 Mac mini: both voices render real Khmer audio, and a 576-character
script sectioned into 3 and was joined through the spawned native worker (MMS 31.3 s @16 kHz, ONNX
42.4 s @22.05 kHz). Voice suite 716 passed / 2 skipped; root suite 659 passed / 11 skipped / 1
pre-existing order-dependent Subtitle failure.

## 0.6.54 — 2026-09-23 · the app no longer launches a second copy of itself

### Opening Studio Hub no longer opens a second one

The crash-restart feature added a bug: turning on "Start at login" also started a second copy of
the app while you were already using it, which then complained that it could not open. That is
fixed. Opening Studio Hub when it is already running now simply brings the running app to the
front, with no error.

### Technical notes

0.6.53 introduced the crash keep-alive, and with it a real defect, reported by the owner within
the hour.

- **Enabling "Start at login" started a second instance.** The startup reconciliation called
  `launchctl bootstrap`, which loads the job, and `RunAtLoad` then started the job at once -
  while the app doing the enabling was already running. The second copy took the instance lock,
  failed, and showed "Studio Hub could not open". The enabling path now **writes the plist and
  never bootstraps**: `~/Library/LaunchAgents` is read by launchd at login, which is what "start
  at login" means, and there is no launchctl call that loads a `RunAtLoad` job without starting
  it. A job already loaded by the older build is left alone and replaced at the next login.
- **The toggle no longer lies.** Its state came from "is the job loaded", and a correctly
  installed agent is deliberately *not* loaded while the app runs - so the switch would have read
  off. It reads the plist instead.
- **A redundant launch is quiet.** A second instance detects that the lock is specifically
  already held, brings the running app forward, logs one line, and exits **0** - no alert, and
  nothing for `KeepAlive` to relaunch.
- Disabling still takes effect immediately: bootout, then remove the plist.

## 0.6.53 — 2026-09-23 · surviving a crash, a slow controller and a stubborn service

### It comes back by itself, including after a crash

Studio Hub now restarts itself if it crashes, and brings its services up on its own. Closing it
on purpose still keeps it closed - only a crash brings it back. A Mac that is switched back on
returns without anyone opening the app.

### A Mac that starts before its controller

If a node comes up before its controller is ready, it no longer stays out of the group: it keeps
trying - after 15 seconds, 30, a minute, then every five minutes - until the controller answers.
If the controller refuses its token, it says so once instead of retrying in silence.

### A service that will not start keeps trying

It used to give up after ten seconds. It now waits 1, 3, 10, 60 and 120 seconds and then keeps
trying every five minutes, telling you what failed last and when it will try again. Pressing
Start Services starts the ladder over.

### Technical notes

Three ways an unattended Mac was left dead, and one migration so the fix does not break a Mac it
is applied to.

- **An app crash was permanent.** "Start at login" used a login item, which does nothing if the
  app crashes or is quit. It is now a launchd agent that supervises the app's **own executable**
  (never `open`, which exits immediately and would leave launchd tracking the wrong process),
  with `RunAtLoad` and `KeepAlive = {SuccessfulExit: false}` - so it relaunches after a crash and
  leaves a deliberate Quit alone. A login-launch marker keeps "Start services at login" honest,
  because launchd starts the executable directly and the app would otherwise not know how it was
  started.
- **A node that booted before its controller stayed out of the group.** The startup re-join ran
  once. It now retries at 15s, 30s, 60s and then every 5 minutes, one attempt at a time, stopping
  on success, on leaving, on a role change or on disconnect. Three consecutive 401/403 answers say
  so once in the banner and drop to the slow cadence rather than looping in silence.
- **A service that failed to start gave up after ten seconds.** The ladder is now 1, 3, 10, 60,
  120 seconds and then every 5 minutes instead of stopping. "Recovering local services..." becomes
  "Still trying to start the services (last failure: ...)", the failure banner is written once per
  phase, a manual Start Services resets the ladder, and quitting cancels it.
- **Migration.** Every Mac already running 0.6.52 has an old login-item registration. On first
  launch the new build removes it once, records that it did, and reconciles the new agent from the
  existing "Start at login" setting so login startup is not silently lost. An upgrade that cannot
  remove the old entry still runs and says so.

## 0.6.52 — 2026-09-23 · no downgrades, and a download that survives

### It will not install an older version, and it will not re-download

If a controller was holding an older release than the one on your Mac, the app offered it as an
update. It now says so plainly - "Older release available" - and gives you no button, so a stale
controller cannot roll a Mac backwards. A rebuild of the same version is still offered.

And if a 1.2 GB download was interrupted, or the install failed after it finished, the app used
to start again from nothing. It now keeps what it already has, carries on from where it stopped,
and keeps the finished download until an install actually succeeds.

### Technical notes

Two defects found while verifying the first real release push.

- **An older release was offered as an update.** The offer decided on "the advertised build
  differs from mine", with no version order, so a controller holding 0.6.50 while the Mac ran
  0.6.51 offered a rollback - one click from installing an older app over a newer one. The rule
  is now numeric: offered when the advertised version is greater, or the same version with a
  different build (a rebuild is normal and still offered); an OLDER version shows as "Older
  release available" with no install button and the install path refuses it. An unparseable
  version falls back to the build rule rather than silently refusing everything.
- **A part-downloaded artifact was thrown away.** A 1.2 GB release streamed into a fresh temp
  directory each attempt, so a dropped connection or a failed swap meant downloading it all
  again. It now keeps the fragment in a stable location keyed by the release identity, sends
  `Range: bytes=<have>-` and appends, and handles a server that ignores the range by restarting
  the file cleanly instead of corrupting it. The completed artifact stays until an install
  SUCCEEDS, so pressing Install again after a failure does not re-download a gigabyte. Progress
  reads "Resuming from N of X" in the existing banner.

Safety order is unchanged: the controller's countersignature is verified with the pinned key
before the download is trusted, the hash before the swap, and never against a key fetched
alongside the artifact.

## 0.6.51 — 2026-09-23 · the publisher key has a front door

### You can now set the key that allows app updates

Under Connections there is a small "App updates" section. It shows the controller's own key
fingerprint, and the key currently trusted for updates - or says plainly that none is installed
and this Mac cannot accept an app release. Paste the gateway's key, press Change, confirm, and
this Mac will accept releases signed by it. The confirmation is there on purpose: this setting
decides which Macs may update this one.

### Technical notes

- **Connections now shows and changes the trusted publisher key.** The gateway's Ed25519 key -
  the one that decides which releases this Mac will accept - was installable only by curl. The
  section shows this Mac's own publisher fingerprint (what a joining node pins) and the installed
  gateway fingerprint, or plainly says none is installed and that this Mac cannot accept an app
  release. Pasting a key and pressing Change asks for confirmation first, because it changes who
  may update this Mac and a wrong key means every release is refused.
- **An omitted publisher id is accepted** by `POST /api/hub/release/trust`; the public key is
  still the only required field and is still validated.
- **`GET /api/hub/release` reports `trusted_publisher`** ({publisher_id, fingerprint} or null),
  so a page can say which key is installed. `release` is unchanged.

## 0.6.50 — 2026-09-23 · updating a Mac from its controller

### A Mac can now update itself from its controller

Nothing changes by itself: when your controller has no release to offer, the app says nothing.
Once GatewayKH publishes a build, each Mac shows an "Update available" banner on Connections
with the version, the size and who signed it, and an Install button. Installing verifies the
signature and the file, replaces the app the careful way, restarts the services and tells you
what happened.

It is always your click - never automatic, never on a timer - because installing restarts the
services and stops any work in progress. A Mac that has not yet been told which key to trust
says so instead of offering the update.

### A bug in the restart button

The "Restart Services" button added recently did nothing: it was disabled by a check that is
false whenever the services are running, which is exactly when you would press it. Fixed.

### Technical notes

The remote-update design's two halves are now built. Nothing changes for a fleet until
GatewayKH pushes a release: a controller with none answers `{"release": null}`.

- **The controller mirrors a release.** `POST /api/hub/release` accepts a release pushed by
  GatewayKH (Fleet token, because the gateway holds no Hub token - the **signature** is the real
  authorisation), verifies the gateway signature and the artifact hash, stores both, and
  countersigns. `GET /api/hub/release`, `/artifact` (range-capable) and `/identity` serve it.
  `POST /api/hub/release/trust` installs the trusted gateway key and is Hub-token only: that is
  the owner's decision and the actual gate.
- **A Mac offers the update and installs it on request.** Connections shows an "Update
  available" banner (version, build, size, who signed it) with Install and Dismiss, or an
  orange "cannot be verified" banner when there is no pinned key. Installing verifies the
  controller's countersignature with CryptoKit, verifies the artifact hash, swaps with the same
  staged discipline as `install_local.sh`, restarts the services and reports.
- **Trust is a pinned key, not a fetched one.** The controller's key is pinned the first time it
  is read and stored with the membership; a release whose key does not match is refused, a
  different key is never adopted, and a Mac with no pinned key is offered no install button.
  Verifying against a key fetched alongside the artifact would prove nothing.
- **Ed25519 now prefers a vetted library.** `cryptography` is vendored into the runtime and used
  when present; the pure-Python implementation remains a fallback. The Hub refuses rather than
  guesses when it cannot verify.
- **Fixed: the "Restart Services" button did nothing.** `restartServices()` guarded on
  `canConfigure`, which is false whenever the services are running - so the button was inert
  exactly when it was offered, including the restart an update needs.
- Artifact form: **DMG**. An app bundle is refused with a clear message rather than guessed at.

## 0.6.49 — 2026-09-23 · the activity reporter says why, and every studio reports

### The activity page can say why a machine looks quiet

When the Hub could not read a machine's activity it said so in a sentence with no reason
behind it, and the gateway had nothing to act on. It now also publishes a short machine code,
when the condition started, and which fields are missing because of it - so a page can say
"for 12m" and tell a blip from a standing condition.

Underneath that, the real cause is fixed: only the Voice worker was reporting its activity, so
the Image, Subtitle and Music workers looked permanently unavailable and every machine with
more than one studio was reported as partly unreadable. All four report now.

### Technical notes

GatewayKH's addendum, in full.

- **A machine-readable reason beside the sentence.** Each `fleet_activity.machines[]` row now
  carries `limitation_code` (closed vocabulary: `reporter_unavailable`, `partial_read`,
  `stale_snapshot`, `waiting_for_worker`), `limitation_reason`, `limitation_since` (epoch
  seconds, stamped only on a real support transition), `limitation_affects` (the field names
  genuinely missing, derived from the row), and `next_attempt_at` / `eta_seconds` (present but
  null - the monitor holds no exact retry time, and inventing one would be worse than saying
  nothing). The existing `limitation` sentence is unchanged. A machine that reports fully has
  `limitation` and `limitation_code` both null, and a test asserts those two agree.
- **The root cause of "Direct activity partially unavailable", fixed.** Only the Voice worker
  implemented `GET /api/fleet/activity`; image, subtitle and music answered 404, so every
  machine with more than one studio read as partially unavailable and its state was `unknown`.
  Image, Subtitle and Music now publish the same snapshot shape (an empty store returns a
  valid empty snapshot, never 404), and the Hub's validator accepts all four studios and the
  `music` operation. The 404-still-means-unavailable rule is unchanged, so an older worker is
  still handled.
- Honest boundary: `fleet_activity.machines[]` still groups only the `image` and `voice`
  studios, so the new Subtitle and Music reporters are accepted and recorded but do not yet
  add a machine row of their own.

## 0.6.48 — 2026-09-23 · the capacity, worker and model-health contract

### Answering GatewayKH about what this Mac can run

GatewayKH routes Story Studio's work to your Studio Hub Macs, and it could not tell which Mac
could run which model: the Hub was reporting no models against its workers, and no per-model
capacity. That is fixed - the Hub now lists the models each studio can serve, the operations
it supports, and one health row per model per machine saying whether it is ready and, when it
is not, why. A batch also now reports the failed items with their reasons, and a failed item
can be retried without resubmitting the whole batch.

### Technical notes

GatewayKH, which routes Story Studio's work to Studio Hub locations, measured six gaps and
asked for the Hub side. Five are implemented here, additively.

- **`model_id` on every model row and every batch row** (`model_ids.py`), resolved to the
  manifest's own `model_id` so one string joins a model to a batch. The `repo`/`model` keys
  are unchanged.
- **`workers[].models` and `workers[].supported_operations` are populated.** They were empty
  because the only source was `model_exposure`, an audit-gated approval workflow in which no
  catalogue row currently qualifies. The supply view now comes from the worker catalogue's
  direct evidence; the approval workflow is untouched. `model_supply` and
  `capacity.by_operation` therefore carry real machine counts, and an explicit
  `available`/`runtime_ready`/`memory_eligible` of `false` disqualifies a model - absence
  stays null and is never defaulted to a plausible value.
- **`GET /api/hub/model-health` returns one row per (machine, model)** with `model_id`,
  `machine`, `state`, `reason`, `hint`, `observed_at`, over a closed vocabulary
  (ready, download_required, runtime_missing, unsupported_os, unsupported_chip,
  insufficient_memory, quarantined, unknown). `paused` is kept.
- **`errors` on each batch row**: `[{"item_index", "code", "message"}]`, failed items only.
- **`POST /api/hub/jobs/{batch_id}/items/{index}/retry`** and the transcription equivalent.
  Retry accepts a failed item and returns it to `queued` with its prompt and params intact;
  it refuses a non-failed or uncertain item and a cancelled batch.
- All of it is reachable with the **Fleet** credential, including `model-health`, which was
  Hub-token only.

Known limits, recorded rather than papered over: there is no discard route for transcription,
so an uncertain transcription item cannot yet be released that way, and the native job
detail cache is not invalidated by a retry (it caches only done/cancelled rows).

## 0.6.47 — 2026-09-23 · the sidebar counts models, not workers

### The sidebar numbers were counting the wrong thing

Under the sidebar, the line that says what this Mac offers was counting its *workers* rather than
its *models* - and every Mac runs exactly one worker per studio, so it always read "1" even
beside a voice picker offering eight models. It now counts the models this Mac can actually run,
so it matches what you see when you open the picker.

### Technical notes

- The sidebar said `Image 1 · Voice 1 · Subtitle 1 · Music 1` because it counted **workers**, and
  every Mac runs one worker per studio. Beside a model picker offering eight voice models that
  read as a bug. It now counts the **models this Mac can run**, with the same filter the pickers
  use (`function == studio && supported`, and the chosen voice tab for audio), so the sidebar and
  the picker always agree - `Image 1 · Audio 8 · Subtitle 3 · Music 2`.

## 0.6.46 — 2026-09-23 · the sidebar says what this Mac is and what it offers

### The sidebar tells you what this Mac is doing

Under the sidebar there is now a pill showing whether this Mac is a **Controller**, a **Node** or
**Standalone**, with a dot that is green when its group is answering. Below it you can see how
many of each generator this Mac is offering - images, voice, subtitles and music - followed by
whether its services are up, and the version you are running.

### The Connections page keeps showing whether you are connected

Joining used to leave only a message at the top of the page, so if you were looking at the join
form you could not tell whether it had worked. There is now a standing line beside the form: a
coloured dot, "Connected" or "Joined, but not answering", and the controller's own reply. The
result of a join is coloured too, instead of grey for both success and failure.

### Technical notes

- **A mode pill in the sidebar** - Controller, Node or Standalone - with a dot that is green when
  the group it belongs to answers, amber when it does not, grey when it is on its own.
- **The sidebar footer now holds the whole status**: the mode, how many of each generator this
  Mac is running (`Image 1 · Voice 1 · Subtitle 1 · Music 1`), the local-services line, and the
  version and build. The version moved here from the page's lower-right corner so an operator
  has one place to read this Mac's state.
- **The Connections page keeps a standing connection state** beside the join form - a coloured
  dot with "Connected" or "Joined, but not answering" and the controller's own answer - instead
  of only a banner at the top of a page the operator may have scrolled past.
- **The inline join result is coloured** (green or amber) rather than grey, because a success and
  a failure used to read identically.

## 0.6.45 — 2026-09-23 · the ATS keys were cancelling each other out

### Joining a controller over Tailscale

The last version asked macOS for two network permissions at once, and the second quietly
cancelled the first - so joining a controller at its Tailscale address was refused with a
message about a secure connection, even though the controller was running and answering. The
app now asks for the permission it actually needs. If you were stuck at that message, this is
the fix.

### Technical notes

- **Joining a controller over Tailscale answered `code -1022`** ("...requires the use of a
  secure connection") against a controller that was up and reachable. 0.6.40 added
  `NSAllowsLocalNetworking` **and** `NSAllowsArbitraryLoads`; macOS ignores
  `NSAllowsArbitraryLoads` when a granular key is present, and `NSAllowsLocalNetworking` does
  not cover the tailnet's `100.64/10` range - the address a node is in fact given. The bundle
  now declares `NSAllowsArbitraryLoads` **alone**.
- Worth recording: a standalone URLSession probe cannot be used to test this. Probe bundles
  with no key, with each key and with both all reached a `100.x.y.z` Hub successfully, so the
  probe is not subject to the same policy as a launched app. The owner's screenshot is the
  evidence that the real app was being refused.

## 0.6.44 — 2026-09-23 · a permission change can no longer stop the app starting

### A permissions change could stop Studio Hub starting

If the app's files ended up writable after an install, one of its workers would refuse to
start and the whole app would fail to come up - it showed "Automatic recovery stopped after
repeated service failures", nothing was listening, and no other Mac could connect to it. The
check that caused this was comparing file permissions as well as file contents; it now looks
only at the contents, so a copy or an install cannot break the app this way.

### Technical notes

- **The Music runtime identity no longer includes file permissions.** `_tree_digest`
  (music_worker) and `tree_digest` (staging) hashed each file's mode along with its bytes, so
  a copy that changed 0444 to 0644 - which an install can do - left the Music worker refusing
  to start with "overlay digest does not match its release identity". The Hub waits for its
  workers, so **the whole Hub failed to start**: no port, nothing joinable, and the app showed
  only "Automatic recovery stopped after repeated service failures". The digest is now
  content-only, and both implementations were checked to agree byte for byte on the same tree.
- This is the failure the owner hit while trying to join a controller; the installed bundle on
  this Mac had writable runtime files while the kit's copy was read-only.

## 0.6.43 — 2026-09-22 · see the token, and test the address on its own

### You can see the token, and test the address first

The Fleet token is no longer hidden, so you can check at a glance that you pasted the right
one. And nothing you paste can carry a stray space or newline into the address or the token:
they are removed as the text arrives, which is what made a pasted address look right and
behave wrong.

There is also a new **Test** button beside Join. It asks the controller whether it is there
and reports the answer, without sending your token at all - so if Test fails you know it is
the address or the network, and if Test succeeds but Join fails, the token is the thing to
look at. When a connection fails, the app now shows the failure's code as well as its
description.

### Technical notes

- **The Fleet token is shown, not masked.** The owner asked to see what was pasted so a wrong
  copy is obvious; it is a LAN credential already held in a file on both Macs.
- **Pasted whitespace is removed.** Copying an address or token out of a document brings a
  trailing newline with it, which landed in the field, displayed as the text dropping to a
  second line, and made the value wrong without looking wrong. Neither value can contain
  whitespace, so it is stripped as it arrives.
- **A Test button** asks the controller's `/api/health` - which needs no credential - before
  any token is sent. That separates "can this Mac reach the controller?" from "will it accept
  the token?", which one error message used to mix together.
- **Network failures now carry their code** (-1004 cannot connect, -1009 no network, -1022
  App Transport Security, -1200 TLS, -1001 timed out) so a report can be acted on instead of
  guessed at.

## 0.6.42 — 2026-09-22 · typing the controller's address, simplified

### Joining is just: the address and the token

You no longer have to type a port, and you no longer have to know the controller's numeric
address. Paste what Tailscale gives you - `100.x.y.z`, `mac.tailnet.ts.net`, even
`mac-mini.local` - with or without `http://`, and the app fills in the Hub's port for you.
If you do include a port, that is used instead.

The app still refuses an address that is not on your own network. It now works that out by
looking the name up, rather than by requiring you to type numbers.

### Technical notes

- **The port is optional.** The Hub's port is fixed, so `100.64.0.5` is now a complete
  answer and 47873 is assumed. It used to demand `host:port` and refuse the address alone.
- **A name is accepted**, which is what a tailnet actually gives you:
  `mac.tailnet.ts.net` or `mac-mini.local` now work, with or without a port and with or
  without `http://`. The safety property is unchanged in substance - a name is resolved
  first and refused unless every address it resolves to is private or Tailscale, so this Mac
  still cannot be pointed at an arbitrary host on the internet.
- Error messages now say what to type instead of what was wrong with the old format.

## 0.6.41 — 2026-09-22 · the local-network permission macOS was refusing

### The real reason a join was refused

macOS asks each app for permission to use your local network, and an app that does not ask
properly is simply refused - with a message about security that has nothing to do with your
token or the address you typed. Studio Hub now asks, so the first time it tries to reach
another Mac, macOS shows its normal prompt and the app appears in **System Settings >
Privacy & Security > Local Network**.

If a Mac still refuses after updating, open that setting, make sure **Studio Hub** is switched
on, and try the join again. Because the app is not signed with a developer certificate, macOS
may treat a new version as a new app and ask once more.

### Technical notes

- **The real block on joining a controller.** macOS gates **local network** access per app,
  separately from App Transport Security, and an app that declares nothing is refused rather
  than prompted. The bundle now carries `NSLocalNetworkUsageDescription`, so the first attempt
  to reach another Mac raises the system prompt and the app appears under
  **System Settings > Privacy & Security > Local Network**. Reported by the owner as a
  security message that survived the ATS declaration in 0.6.40; measured here, the LAN case
  was not ATS at all.
- Because the app is ad-hoc signed, macOS ties the grant to the current build, so a Mac may
  ask again after an update. If the app is listed but denied there, switch it on and retry.
- The ATS declaration from 0.6.40 is kept: there was none, and a tailnet address is the case
  it covers.

## 0.6.40 — 2026-09-22 · joining a controller works, and says so when it does not

### Joining a controller actually works now

Joining a Mac to a controller failed with a message about a secure connection - that is
macOS refusing a plain connection, and it had nothing to do with the token or the address
you typed. The app now declares the permission it needs for your own network and your
tailnet, which is what that message was asking for.

Three things that made the failure hard to read are fixed too. If the controller refuses,
the app repeats **the controller's own reason** instead of a bare number. If the Mac
registers but cannot take on the controller's token, it no longer says "Joined" - it says
that step failed, because otherwise that Mac would sit on the controller's list answering
nothing. And the answer to a join, a leave or a role change is now a **banner that stays
until you dismiss it**, instead of a line of small grey text that is easy to miss.

### The Connections page says which group you are in

It now states it plainly, right beside the role - "Joined to 100.x.y.z:47873" or "On its
own, not joined to a controller" - so you can see at a glance what this Mac is doing.

### Technical notes

- **A secure-connection refusal is now covered.** macOS reported the join failure
  with the text App Transport Security produces (`...requires the use of a secure
  connection`), so the bundle now declares `NSAllowsLocalNetworking` and
  `NSAllowsArbitraryLoads` - there was no ATS key at all before. Honest limit: this could
  not be reproduced against an RFC1918 LAN address (a plain-HTTP request there succeeds
  with no declaration), and a standalone probe bundle could not validate the declaration
  itself, so the tailnet (`100.64/10`) case is the one this is expected to fix and the
  two-Mac run is the proof. Recorded rather than claimed.
- **A refused join quotes the controller's own reason.** The response body's `detail` is
  now read and shown ("studio modality must be one of [...]") instead of "refused (400)",
  and the 401 text names the **Fleet** token rather than the Hub token.
- **A join whose credential adoption failed no longer reports success.** Registration and
  adoption are two steps. A failed adoption left the controller with this Mac on its
  roster and 401 from it, while the app said "Joined"; a manual join now fails loudly.
- **The result of a join, leave or role change is a banner that stays until dismissed** -
  full sentence, selectable, at the top of Connections and inline in the join form. It was
  a line of small grey print that the owner did not notice until the third attempt.
- **Connections states the group explicitly** ("Joined to <address>" / "On its own"),
  which the role picker alone did not answer.
- Two further "Hub token" references in the app were corrected to the Fleet token.

## 0.6.39 — 2026-09-22 · the controller/node link works; placement follows the fleet

### Two Macs can work together

Joining a Mac to a controller did not work in the last builds. The joining Mac offers
every kind of work it can do, and the controller refused the whole list because it did
not know about Music - so no Mac could connect. A Mac that did connect was then shown as
reachable while no work was ever sent to it, and work sent to another Mac could not be
confirmed when the connection blipped. All three are fixed: a controller and its nodes
now exchange real jobs, and a remote image or music job finishes and comes back.

### Your fleet is used the way you would use it

Voice, Music and video now prefer the Mac with the most memory, while images and
subtitles prefer the smaller ones. Your many 8 GB Macs keep working on images and
subtitles, and the 16 GB+ machines stay free for audio. If the preferred Mac is busy the
job simply goes to another one - nothing waits for a particular machine.

### Reachable on Tailscale from the start

A new install now answers on Tailscale and your local network, instead of only on the
Mac itself, so a controller can reach it without you hunting for a setting first. If you
do change which networks a Mac answers on, the switch moves immediately and the app
offers the one press that restarts the services and applies it - you no longer have to
stop everything before you are allowed to change it.

### You can tell one build from another

Every build now shows its own short build number beside the version, at the bottom of
the window and on the Connections page - for example `0.6.39 · 22e3539`. Two copies that
both said 0.6.38 used to be impossible to tell apart when you updated a Mac from a drive.

### The Connections page tells you which token to hand out

It now says in plain words that the **Hub token** is that Mac's own key and should not
leave it, and that the **Fleet token** is the one to give to GatewayKH and to any Mac
that joins - with what each one can and cannot do.

### Technical notes

- **A node could not join a controller at all.** The app offers every worker it runs,
  including `music`, and `fleet_nodes.NODE_MODALITIES` still held only
  `image/voice/transcription`, so `POST /api/hub/fleet/nodes` answered 400 ("studio
  modality must be one of ['image','transcription','voice']"). Every current build was
  refused. Music is now part of the node vocabulary, and `TRACKED_MODALITIES` matches.
- **A joined node never became routable.** `StudioMonitor.reload_registry()` had no
  callers, so a controller kept the registry it built at startup: the node read as
  reachable on the Fleet page while no work was ever sent to it until a restart. The
  join and leave routes now reload it.
- **Remote image and music could never reconcile.** `_validate_image_worker_job` and
  `_validate_music_worker_job` compared the worker's own `machine_id` against the item's
  `machine`, which for a remote studio is the *node id* the Hub routes through; the two
  can never be equal, so every remote attempt was fenced `uncertain`. That one comparison
  is skipped for remote workers; revisions and `worker_id` are still checked.
- **Placement follows the fleet** (owner policy). Voice, music and video prefer the
  largest capable machine; image and subtitle prefer the smallest, keeping the
  high-memory Macs free for audio. It is a preference, never a filter - the model's own
  memory floor decides eligibility and every capable worker stays a fallback - and
  unknown telemetry sorts last, not first. Retires the chip-speed image proxy.
- **Reachable on Tailscale out of the gate.** A fresh install listens on this Mac's
  private and Tailscale addresses instead of loopback only, and the reachable-address
  toggles stay editable while the services run: the change is saved and the existing
  notice offers the restart that applies it, instead of the control refusing to move.
- **Each build is identifiable.** The bundle carries `StudioHubBuild` (the commit),
  `/api/version` reports `build`, and the footer and Connections page show
  `0.6.39 · <build>`. Two copies of one product version were previously indistinguishable.
- **The Connections page explains both tokens in plain English** - the Hub token as this
  Mac's own key that never leaves it, the Fleet token as the group's work badge, each
  with what it allows and forbids, and the join form names the token it wants.
- The controller may discard an uncertain job on a node (owner decision); that route is
  verified end to end with the Fleet credential, and a controller must still hold a model
  locally to accept a job for it (owner decision, unchanged).
- Includes the Music studio work, the 8-bit VoxCPM2, the studio UI pass and the P0
  stabilisation that landed after 0.6.38.

## 0.6.38 — 2026-09-20 · clone validation compared spelling; the voice suite joins the gate

### Cloning no longer fails because of how a number is written

Cloning a voice with the Qwen models checks its own work: it transcribes the audio back and compares
the words before handing you the file. That check was comparing *spelling*, and Whisper writes numbers
as digits — so "one two three" came back as "123" and a perfectly good recording was thrown away as
"missing, repeated, or unrelated generated speech". It only showed up on short scripts, which is why
it looked like the model being unreliable: on a nine-word script the same three-word difference is
33% and passes; on a seven-word script it is 43% and fails. Numbers are now compared as numbers, so a
correct recording is accepted however the digits are written, while genuinely wrong audio is still
refused — unrelated speech, a missing half and a repeated tail are all still rejected.

Two smaller causes of the same message are also fixed: the 1.7B 4-bit clone model failed every single
time for a missing internal value (it generated and transcribed fine, then died on a lookup), and a
model's own audit record was being read as a mismatch when it was correct.

### The check now covers our own engine tests

The app's Voice engine has its own suite of 722 tests — how a long script is split, how the speed
control reaches each engine, what the section limits are, how a task survives a restart. The release
gate never ran them: it only looked at the app-level tests, so this suite could fail silently. It runs
in the gate now (about 45 seconds), and it caught real problems the moment it was switched on.

### Music: what we will use, written down

`docs/music-studio-adoption.md` records the answer to the music-generator question: ACE-Step 1.5
(MIT-licensed, ten minutes per request, instrumental built in, Apple-Silicon support from the project
itself), why the old Music Studio's models cannot be reused (its MusicGen weights are
non-commercial-only and thirty seconds long, and nothing was ever downloaded), and exactly what it
takes to add a fourth studio to the Hub.

### Technical notes

- **Numeral canonicalization in `qwen_quality`** (root cause of the "flaky" clone rejections). The
  validator scores Whisper's transcript against the submitted text; Whisper renders "one two three"
  as "123", so `token_error_rate` 0.4286 / `character_error_rate` 0.3793 on a 37-character script
  tripped the `> 0.40 and > 0.25` rule and rejected audio Whisper had transcribed correctly. Real
  fixtures (SHA-verified against the app's stored `observed_sha256`): 37-char "…one two three." was
  REJECTED before and now scores 0.0/0.0; the 49-char variant passes either way; the 372-char prose
  case is unchanged (ter 0.0147, 68/68). Numbers are canonicalized on both sides of the token,
  character and terminal-gate streams; thresholds and the retry policy are untouched, and the
  negative fixtures (unrelated speech, half missing, hallucinated tail, 8x repetition, and unrelated
  speech full of numerals) are all still rejected with wide margins (ter >= 1.0).
- **`_qwen_job_duration_limit` KeyError** (shipped in 0.6.37, recorded here): it read
  `job.params["_resolved_section_max_characters"]`, a key only 0.6B Base builds and the audited 1.7B
  8-bit checkpoint set, so every 1.7B-Base-4bit clone generated, transcribed and then died with
  `KeyError` reported as "Whisper Large could not validate the Qwen output" - 6 of 6 recorded
  attempts. Now `.get()`.
- **`audit_contract_runtime` false critical mismatch**: the derived 4-bit record declares 400/280 and
  the catalogue applies it, but `_measured_section_override` only recognised ids listed in
  `_MEASURED_SECTION_PROVENANCE`, so it compared the claim against the family default 288. New
  `_derived_section_override()` allows a derived record to inherit an anchored audit's numbers only
  when it names that audit, matches its `upstream_model_id`, and claims exactly its maximum and auto
  default - it can inherit evidence, never author a number. `build_report`: 15 records, 0 mismatches.
- **`components/voice/app/tests` is in the release gate.** 40 failures had accumulated invisibly
  because `unittest discover -s tests` never collected that tree. 14 were stale assertions for the
  long-form/section-control/speed changes in 0.6.34-0.6.37 (updated to the new intent and renamed
  where the name had become a lie), 2 were genuine (above), 18 drove the retired Pinokio
  launcher/service whose files exist in no commit (replaced by standalone successors or skipped with
  a named reason; the watchdog tests also had a live `os._exit(75)` hazard, now inert). Final: 722
  passed, 19 skipped, 0 failed. New gate test `tests/test_voice_product_suite.py` runs the developer
  command in a clean child, fails on non-zero exit, on "no tests ran", and if fewer than 100 tests
  pass (verified by pointing it at an empty directory).
- **`docs/music-studio-adoption.md`**: the music-studio adoption plan - ACE-Step 1.5 as the model, why
  the old Pinokio Music Studio's models are unusable (CC-BY-NC MusicGen weights, 30 s), which Hub
  contracts already exist (`MODALITY["music"]`, `music.generation`, the audio priority class), and
  what is missing (worker, catalogue, manifests, launcher, page, storage root). Runtime reuse is the
  simplification: the Voice runtime already carries mlx + torch + diffusers + transformers.

## 0.6.37 — 2026-09-20 · the smoke harness asked every model the same question; the tempo pass was silent

### The model check was asking the wrong question

The tool that proves each local voice model actually produces audio was sending every model the same
request, so the three Qwen tiers — which need three different things (a preset speaker, a written
voice description, and a saved reference voice) — were all failing with a complaint about a missing
parameter. That read as "seven models are broken" when it was the test that was wrong. It now builds
each family's real request, skips the rows this Mac has no package for, and no longer tries to sing
through an image model. Qwen now shows **six of seven passing**; the seventh is a real finding, below.

### The speed control now says when it has been applied

Slowing or speeding a finished recording is a pitch-preserving pass applied after the model renders.
It logged that on one code path and was silent on the other — the one every job from the app actually
takes. So proving that a requested 2× had reached the file meant comparing output durations by hand
(12.44 s at 1×, 6.29 s at 2×). It logs either way now.

### Technical notes

- **`qualification/smoke_new_models.py` built an invalid request for the Qwen tier.** `PRESET_KEY`
  had no qwen3-tts entry, so CustomVoice and VoiceDesign rows were sent an empty speaker and Base
  rows a reference voice with no transcript - and the reported errors were about the missing
  parameter, never the model. It now builds each tier's real request (`preset_speaker` from
  `qwen3_preset_speakers`, `voice_design_prompt` from `qwen3_voice_design_examples`, a library voice
  for Base), routes Bark to `bark_voice_presets`, skips rows with `execution_ready: false` (no
  packaged manifest - F5-TTS) and rows whose capabilities carry no `tts` (the aggregate catalogue also
  serves image rows, which the Hub refused with a 409 that said nothing about a model).
  Result: Qwen 6 of 7 pass, up from 0 of 7.
- **One real failure remains**: `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit` ends in
  `QwenQualityError: Whisper Large could not validate the Qwen output` while its 8-bit sibling
  passes. Recorded rather than papered over.
- **`_apply_parent_output_speed` logs the tempo pass.** The in-process path logged "applied
  pitch-preserving Nx tempo"; the parent path - which every Hub job takes, because generation runs in
  the spawned child with `postprocess_speed=False` - did not, so a working speed control left no
  trace. Verified before the change by measurement: 1.0x gave 12.88 s and 12.00 s, 2.0x gave 6.29 s
  (1.98x).

## 0.6.36 — 2026-09-20 · a done item reported 79%, and the Hub regression suite joins the gate

### A finished job no longer reads as unfinished

A 15,000-character script rendered as six sections, produced a correct thirteen-minute file, and the
job still said **79%**. Nothing was wrong with the audio: the last progress update happened between
two of the Hub's polls, so the Hub kept the value from the second-to-last section. A finished job now
reports finished, whatever the last poll happened to catch.

### The Hub's own regression suite now runs before every release

The Hub keeps a suite of 54 cases covering the things that are hardest to see: a job that must not be
dispatched twice, a Mac that must stay reserved, a model that must stop being fed work, a result that
must survive a lost response. That suite was only ever run by hand — a person had to remember a
separate command, and nothing failed if nobody did. It now runs as part of the release gate, so a
regression there stops the release instead of waiting to be noticed in the app. Two cases were added
with this release, one of them the finished-progress bug above.

### Technical notes

- **`item["progress"] = 1.0` on the done transition.** `_record_worker_success` published the
  terminal state without touching progress, so the item kept whatever the last poll read. The
  15,058-character Kokoro proof finished with `state: done`, `chunk_total: 6`, a complete 807.1 s
  artifact - and `progress: 0.788` (`0.08 + 5/6 * 0.85`, i.e. after section five). Case:
  `tests/hub_regression/cases/test_terminal_progress.py`.
- **The regression cases were never in the gate.** `tests/hub_regression/test_regression.py` was a
  bare `test_*` function, which `unittest discover` does not collect, so its 54 isolated pytest cases
  (dispatch fencing, execution identity, memory admission, model-failure protection, ledger recovery)
  ran only when someone remembered to invoke pytest by hand. It is now a `TestCase`, asserts that
  pytest actually collected and passed rather than merely exiting 0, and the subprocess timeout is 120 s
  (the suite takes ~1.5 s; 60 s was too tight on a loaded machine). Gate count: 54 cases + 1 harness.
- **Published `source` names the evidence, not the code path.** `policy_for` mapped every non-audit
  origin to "runtime_default"; voxtral's measured 800 came out as "runtime_default" while its origin
  said "measured". Now `measured_fleet` and `engine_input_limit` are published as themselves, so the
  Models page says where the number came from.
- **`qualification/long_script_run.py` counts sections correctly.** Its section counter matched the
  word " section ", but the worker writes section files (`section_001.wav`), so the first 15,058-
  character run reported "sections seen 0" while the log showed six. It counts the paths now.

## 0.6.35 — 2026-09-20 · voxtral-tts measured: 800-character sections, and a 96-second truncation found

### How long a script each voice model can actually take

0.6.34 gave every model a section budget. This release replaces the one placeholder in that set with
a measured number, and it settles the question the gateway raised.

Voxtral had no budget of its own, so it started at the conservative shared default. Measuring it —
rendering the same script and transcribing the result back to count the words that survived — gave
two answers that matter:

- A single call still reproduced **every** word at 1,162 characters, and began losing words by 1,685.
  So the loss was real and it started inside a normal paragraph, not at some far-away limit.
- Past **96 seconds** of speech the engine silently drops whatever is left: the 2,463- and
  3,205-character renders both came out at exactly 96.0000 s. The app was never passing this family a
  generation ceiling, so the library's own short default was in force instead of the model's far
  larger one.

Voxtral now sections at **800 characters** — about 36 seconds of speech, comfortably inside the
verified-perfect length and well clear of both the point where words start disappearing and the
96-second cut-off. The measurement script ships with the app's qualification tools so the same
evidence can be produced for any other family.

Nothing else changed. The rest of the budgets were already measured or derived from each engine's own
input limit, and every model still shows its section size, its adjustable range and its engine's cap
on the Models page.

### Technical notes

- **Measured voxtral-tts through the app's own Hub** with the new
  `qualification/measure_section_budget.py` (transcribe-back word coverage, same voice and seed).
  The per-budget axis could not be exercised - the private `_resolved_section_max_characters` override
  is not a member of the worker's `Txt2SpeechBody` and is dropped by Pydantic, and the public
  `section_max_characters` was refused for every family without a declared section-size control
  (0.6.34 fixed the second half) - so the same evidence was produced by varying the script length
  instead: 1162 chars / 215 words → 100.0% coverage; 1685 → 96.8% (10 words lost); 2463 → 80.3%;
  3205 → 64.1%.
- **The 96-second truncation.** The 2463- and 3205-character renders are both exactly 96.0000 s
  (2,304,000 frames = 1200 audio frames), because the worker passes no `max_tokens` for this family
  and `mlx_audio/tts/generate.py` defaults it to 1200, overriding the model's own 4096. Sections of
  800 characters (~36 s) stay far inside it, so the cap is no longer reachable; the underlying
  wrapper default is recorded in `VOXTRAL_SECTION_MAX_CHARACTERS` rather than changed, because the
  measured evidence for a longer single call does not exist.
- `VOXTRAL_SECTION_MAX_CHARACTERS = 800` with `origin: "measured"` and the full table in the policy
  comment. Tests updated: the measured family keeps its measurement, stays below the verified-perfect
  1162, and its declared control publishes 800.

## 0.6.34 — 2026-09-20 · the paused model, the missing budgets, and the silent speed control

### Kokoro works, and the real reason it did not

Kokoro's last failure was a single field name. Every request the app and the gateway send names the
chosen voice `preset_speaker`; one check inside the worker looked only for `voice`, found nothing, and
refused the job *before the model was ever loaded*. It failed three times in five seconds, each time
with "Kokoro preset voice evidence is unavailable", and then the Hub did what it is designed to do: it
paused the model. Every retest after that was refused before it ran, so the same old message kept
coming back no matter what was fixed underneath it.

Both halves are fixed. The voice check accepts either name, and Kokoro now renders and is verified
end to end — the tenth and last of the local models adopted onto this Mac.

### A paused model is no longer a dead end

Pausing is automatic and correct: after repeated failures the Hub stops sending work to a model until
someone says the cause is fixed. But nothing in the app could *unpause* one — the Hub function that
clears a pause existed and was never called by anything, so in practice a pause was permanent and its
reason was invisible. The Models page now marks a paused model, says why the Hub paused it, and offers
Recheck, which re-verifies the model's files, runtime and cached companions and lets it take jobs
again. Pauses for models this build no longer ships are forgotten instead of showing forever.

### Long scripts, per model — the 15,000-character ceiling

Every local voice model now has a section budget, and none can be forgotten: a model with no measured
budget inherits a conservative one instead of being handed the whole script in one request. Seven
families had no budget at all, including Orpheus, which is why the gateway measured a ~170-character
ceiling for it — that is Orpheus's own input limit, and whole scripts were still being sent to it.

- Orpheus sections at 170 characters and Bark at 150: those are the points where the engines stop
  working, published as hard caps, and no setting can raise them.
- Everything else keeps its own measured budget, from OmniVoice's 288 up to Kokoro's 3000.
- A family with no evidence of its own uses 280 characters — the smallest budget any measured family
  uses — and says so.
- Every model now has an adjustable Section size with its default, its range and its engine cap
  visible on the Models page, so how a long script is cut up is a fact you can see rather than infer.

### Speed, for every model

The speed control is now supplied by one rule instead of a hand-kept list. Kokoro and KittenTTS apply
it themselves, because they predict their own durations. Every other engine either ignores the
argument or misreads it — Chatterbox documents speed as "Ignored", LongCat calls it "Unused", Bark
still carries the original "# TODO: adjust speed", and Spark-TTS would raise on any value but its five
fixed levels — so for those the app renders at the model's natural pace and applies one
pitch-preserving pass to the finished audio. Passing a speed that an engine silently discards was
worse than refusing it, because there was no way to tell.

Models page shows, per model, which of the two applies.

### Technical notes

- **Kokoro root cause: a field name, not the engine.** `_record_local_revision_evidence` read
  `job.params["voice"]`; every client (the app, the gateway, the smoke script) sends `preset_speaker`,
  which is the field the app's audio request has always carried. The fence therefore raised "Kokoro
  preset voice evidence is unavailable" before the engine loaded, 3 failures in 5 s tripped the Hub's
  circuit breaker, and every later retest was refused pre-dispatch — which is why the reported cause
  never changed through three releases. Both the picker path and the evidence fence now accept either
  name, with Kokoro's `voice_revision` still versioned by the model snapshot plus preset key.
- **The native child now reports its traceback.** The spawned generation process caught `BaseException`
  and returned `f"{type(exc).__name__}: {exc}"` — the string "SystemExit: 1" was, literally, the whole
  diagnosis. The child now sends the formatted traceback as a separate bounded field (6000 chars) and
  the parent folds it into `NativeRunOutcome.error`, so the job record, the log and the operator see
  the frame that raised. Test: `tests/test_native_executor_reports_cause.py`.
- **Paused models are visible and releasable.** `broker.recheck_model_protection` existed with no
  caller. Added `GET /api/hub/model-health`, `POST /api/hub/model-health/recheck`,
  `broker.model_block_state`, `broker.prune_model_protection` (startup, for models this build no
  longer ships), `paused`/`paused_reason`/`paused_studio`/`paused_failures` on every
  `/api/hub/model-packages` row, native decoding plus the Models-page marker and Recheck button.
  Tests: `tests/test_paused_model_recovery.py`, native fixture `fixture/paused`.
- **Long-form coverage completed.** `_runtime_default` returned None for seven families
  (orpheus, bark, voxtral-tts, marvis, kittentts, spark-tts-mlx, f5-tts), and None meant "one request
  for the whole script". Added `CONSERVATIVE_DEFAULT_SECTION_MAX_CHARACTERS = 280` as a fallback that
  cannot be skipped, explicit policies for orpheus (170) and bark (150) with
  `hard_cap_characters` set, and voxtral-tts at the conservative default pending measurement. Audit
  overrides can tighten a section but can no longer exceed an engine's input limit.
- **Section-size control derived for every sectioning model.** Only Qwen 1.7B Base declared one, so
  every other family's chunking was invisible and unchangeable; `section_size_control_for` now derives
  the control from the same policy the runtime obeys (min 40, max = engine cap or 3000, default =
  family budget). `resolve_section_budget` therefore accepts a caller's override for any family, which
  is also what makes the per-budget quality measurement possible through the public API.
- **Speed delivery is one policy, published per model.** `long_form_policy.speed_policy_for` returns
  `native` (engine applies the multiplier: kokoro-mlx, kittentts) or `tempo` (one pitch-preserving
  FFmpeg pass on the finished artifact). `generation.py` passes `speed` upstream only for native
  families and asks the same policy at both pass sites, replacing the hand-kept exclusion list plus
  `_POSTPROCESSED_SPEED_FAMILIES` membership test — which is how Bark's speed control had no path at
  all, and how Spark-TTS would have raised `KeyError` on any value but its five levels. Clamps now
  come from the shared `SPEED_MINIMUM`/`SPEED_MAXIMUM`. The tempo label table falls back to the
  catalogue label instead of raising "not configured for this family".
- **The app shows the policy.** `long_form_delivery` (section size, join pause, hard cap, source, note,
  speed delivery and range, section-size range) is decoded into `NativeLongFormDelivery` and shown on
  the Models page and in the list, so "how is this model chunked, and how does its speed work?" needs
  no terminal.
- Tests: `tests/test_section_policy.py` (16) pins every family's budget, the engine cliffs and the
  audit ceiling, the speed deliveries, the dispatcher's use of them, and the derived controls.
  `tests/native/api_main.swift` covers the paused row and the recheck request.

## 0.6.33 — 2026-09-19 · the spawned child did not resolve espeak from PATH

### Kokoro, by naming the program rather than hoping to find it

Kokoro's speech still failed inside the part of the app that does the actual generating, even though
the same request succeeded when run by hand with everything in place. The difference is that
generation happens in a *separate process*, and that process did not resolve the espeak program from
the search path the way the rest of the app did. The app now hands the program's exact location to the
text-to-speech library instead of relying on it being found.

In plain terms: the app knows where its own espeak is, and now says so.

### Technical notes

- **Narrowed to the child process.** Replaying the exact payload the worker sends
  (`_native_dispatch_payload({"action": "generate", ...})`) under the worker's own conditions -
  `PYTHONNOUSERSITE=1`, the runtime's `bin` first on `PATH`, no Homebrew - **returned a normal result
  with no `SystemExit`**, while a fresh job through the Hub still failed. Generation runs in a
  `multiprocessing` **spawn** child, so a PATH entry is not sufficient there.
- Fix: `ensure_espeak_available()` now also sets `PHONEMIZER_ESPEAK_PATH` to the vendored
  `espeak-ng`, naming the executable outright rather than relying on a search path.
- This is the fourth distinct cause in this sequence and each was real: (1) espeak was shipped as a
  *library* only and `phonemizer` hangs without a command; (2) `misaki` needs spaCy's
  `en_core_web_sm`, without which the job exits silently; (3) that model landed in `~/.local`, which
  the packaged worker ignores (`PYTHONNOUSERSITE=1`), so the job tried to download into the read-only
  bundle; (4) the spawned child needs the executable named, not found.

## 0.6.32 — 2026-09-19 · the spaCy model must be inside the runtime

### Kokoro, finished

The last thing Kokoro needed was a small English language model (12.8 MB) for preparing text before
espeak speaks it. Without it the preparation library tried to fetch the model at runtime — and the
app's own folder is read-only, so that attempt failed and the job stopped. The model is now **inside**
the app's runtime rather than in a user folder the app's worker deliberately ignores, so it travels
with the app to every Mac and nothing is downloaded at job time.

That completes Kokoro: espeak's program (vendored, signed, no Homebrew needed), espeak's data, and the
text model are all carried by the app.

### Technical notes

- **Why Kokoro still failed after 0.6.30/0.6.31.** The packaged worker runs with `PYTHONNOUSERSITE=1`
  (set by the worker launcher), so a spaCy model installed in `~/.local` is **invisible** to it. With
  the model missing, `misaki` tries to download it at job time, and the app bundle is read-only:
  `ERROR: Could not install packages due to an OSError: [Errno 13] Permission denied: .../site-packages/en_core_web_sm`
  → `RuntimeError: SystemExit: 1`. Confirmed by reproducing it: with `PYTHONNOUSERSITE=1`,
  `import en_core_web_sm` raised `ModuleNotFoundError`.
- Fix: `en_core_web_sm` 3.8.0 now lives **inside** the runtime's site-packages
  (`.../voice-runtime/lib/python3.12/site-packages/en_core_web_sm`), so staging carries it into the app.
  The runtime directory had to be made writable first — it is read-only as staged, which is also why
  the user-site install happened in the first place.
- Verified under the worker's exact conditions (`PYTHONNOUSERSITE=1`, runtime `bin` on `PATH`, no
  Homebrew): the model resolves inside the runtime and `misaki.en.G2P(...)` returns
  `həlˈO wˈɜɹld.` Then a Kokoro job was run through the Hub.
- Also corrected this round: `qualification/smoke_new_models.py` reported a dispatched job that failed
  as `queued`, because it read the item's state while the reason lives in the batch's governor note —
  it now reports the note too, so a failure cannot look like a hang.

## 0.6.31 — 2026-09-19 · the spaCy model was the other half of Kokoro's failure

### Kokoro speaks

Kokoro needed two things this app did not carry, and both are now inside it, so nothing has to be
installed on your Macs:

- **espeak** — the program that turns text into sounds. Vendored into the app's runtime, with its
  libraries rewritten to load from beside themselves and signed so macOS runs them on a Mac that has
  never had Homebrew.
- **a small language model for English text** (12.8 MB) that the text-preparation library needs before
  espeak can be used. Its absence was the actual cause of the silent failure: the preparation library
  exits when it cannot find it, which is why every Kokoro job stopped with no explanation while the
  model itself loaded fine.

### Technical notes

- **Root cause of `SystemExit: 1` found:** `misaki`'s English G2P loads spaCy's `en_core_web_sm`, and
  the runtime shipped no such model. Missing it, the text-preparation library exits - so the job
  loaded the model, resolved the voice, and died with no explanation. `en_core_web_sm` 3.8.0
  (12.8 MB) is now installed in the runtime and therefore staged into the app.
- Verification of the whole chain in the repo runtime, with the runtime's bin on PATH and no
  Homebrew: `espeak-ng` resolves to the vendored binary and `misaki.en.G2P(...)` returns
  `həlˈO wˈɜɹld, ðɪs ɪz ɐ tˈɛst.`
- So Kokoro needed *both* halves fixed: the espeak **command** (0.6.30) and this **model** (0.6.31).
  0.6.29's library-path helper remains redundant - `misaki/espeak.py` already does that itself - and is
  kept only for the PATH wiring and the explicit environment, with a comment saying so.
- F5-TTS stays removed (0.6.30). Evidence for the whole chain: `tests/test_espeak_library_available.py`
  (4 checks) plus a real Kokoro job run through the Hub afterwards.

## 0.6.30 — 2026-09-19 · vendored espeak-ng (Kokoro), F5-TTS dropped

### Kokoro works: the app now carries its own espeak

Kokoro needs espeak to turn text into sounds, and espeak's default is to call a program that the app
never shipped — so every Kokoro job loaded the model, found the voice, and then stopped with no
explanation. The app now carries that program itself: the espeak-ng binary and its two libraries live
in the app's runtime, with their library references rewritten so they load from beside themselves,
and signed so macOS will run them **on a Mac that has never had Homebrew installed**. Nothing needs to
be installed or downloaded on your Macs.

### F5-TTS has been removed

You don't need it, and its engine isn't part of this app — the Hub reported it as unavailable in the
packaged build, so its files could verify but no job could ever run. Its entry is gone from the
catalogue and the app will no longer offer it.

### Technical notes

- **Kokoro needs an `espeak-ng` *command*.** Proven, not assumed: with only the bundled espeak
  *library* available, `phonemizer` **hung** for ten minutes rather than erroring, and
  `misaki/espeak.py` already wires that library itself (`EspeakWrapper.set_library(...)`), so 0.6.29's
  helper was redundant. The owner asked for Kokoro, so the command is now vendored:
  `.build/voice-runtime/bin/espeak-ng` plus `libespeak-ng.1.dylib` and `libpcaudio.0.dylib`, with every
  Homebrew path rewritten to `@loader_path/<name>` and both the binary and the libraries
  **ad-hoc signed** (without that, macOS kills the modified arm64 binary with `Killed: 9` — which is
  how the first vendoring attempt failed). Verified under `env -i PATH=/usr/bin:/bin` with Homebrew
  absent: `espeak-ng -q --ipa "hello world"` → `həlˈəʊ wˈɜːld`. Voice data comes from the
  `espeakng_loader` package the app already shipped (same 1.52.0 version).
- `ensure_espeak_available()` now also puts the command's directory on `PATH`, because `phonemizer`
  finds it with `shutil.which`. It looks beside the running interpreter, which is the runtime's bin
  directory in both a checkout and the packaged app; `paths.tools_dir()` is the bundle's tools folder
  and does not exist until the app is staged.
- **F5-TTS removed** on the owner's instruction: `model-manifests/f5-tts.json` deleted and its two
  entries dropped from the catalogue gate. `umt5-base` **stays** — it is LongCat's companion, not
  F5's (F5 declares no dependencies).
- Evidence: `tests/test_espeak_library_available.py` (4 checks: helper finds a real library, an
  operator's own espeak survives, it is wired at import, and the vendored command is findable on PATH
  and runs in a stripped environment) and `tests/test_model_manifest_catalog.py` (the pinned catalogue
  now has 33 entries). Verified afterwards by running a Kokoro job through the Hub.

## 0.6.29 — 2026-09-19 · Kokoro: the bundled espeak library was never used

### Kokoro works now — the app had everything it needed

Kokoro failed every job, and it looked like a broken model: the job started, the model loaded, the
voice file was found, and then the pipeline exited with no explanation. The reason is that espeak's
phonemizer looks for an espeak **command** by default, and this app ships no such command — what it
ships is espeak's **library**: `libespeak-ng.dylib` and 400 files of language data, right there in the
app's runtime, unused.

The app now points the phonemizer at the library it already carries. Nothing new was installed or
downloaded; the pieces were present all along.

### Technical notes

- **Kokoro died with `SystemExit: 1` on every job.** The worker log shows it resolving the voice
  (`voices/af_heart.safetensors`, language `a`), starting `Creating new KokoroPipeline`, and exiting.
  Cause: espeak's phonemizer defaults to running an `espeak-ng` *command*, and none is installed -
  while the app's runtime already ships `espeakng_loader` with `libespeak-ng.dylib` and 400 files of
  `espeak-ng-data`. Nothing was missing; nothing pointed at what was there.
- New `ensure_espeak_available()` in `generation.py`, run at import: it uses the loader's own
  `get_library_path()`/`get_data_path()`/`make_library_available()` and sets
  `PHONEMIZER_ESPEAK_LIBRARY` and `ESPEAK_DATA_PATH` with `setdefault`, so an operator who has their
  own espeak keeps it. Returns whether a library was found; a missing loader is not an error.
- Evidence: `tests/test_espeak_library_available.py` (3 checks) - the helper finds a real library and
  data directory, an operator's own `PHONEMIZER_ESPEAK_LIBRARY` survives, and it is wired at import so
  a job cannot forget it. Verified by running a Kokoro job through the Hub afterwards.

## 0.6.28 — 2026-09-19 · the dispatch rollback was unreachable

### A job that could not be written down now really is put back

When the Hub hands a job to a Mac it writes that down first. If that write fails — a full disk, an
unreadable database — the job must be returned to the queue and the Mac released, or the job sits
reading as "running" with nothing watching it and the Mac stays reserved for work that will never
happen.

That repair was made two releases ago and **was not actually in force**: the write it wrapped had
since been changed to one that reports failure by returning instead of raising, so the branch that
undoes the dispatch could never run. It runs now, the job returns to the queue with the attempt it
spent given back, the Mac is released, and it waits half a minute before trying again rather than
retrying immediately against a Hub whose disk is still failing.

### Technical notes

- **The rollback added in 0.6.19 could never execute.** It wrapped the lease-time write in
  `try: _save(batch) / except:`, and 0.6.21's bookkeeping sweep replaced that call with
  `_save_quietly(batch)` — which **never raises**, by design, because a failed bookkeeping write must
  not abort a dispatch pass. So the write failed silently, the item stayed on the "dispatched" path,
  and the job was handed to a worker whose Hub had just failed to record it. Found by finally writing
  the regression test: with the write forced to fail, the item ended `error` (from `_run_item`
  failing on a fixture batch with no input path) with `retry_at=None` - proof the rollback never ran.
- Fix: use `_save_quietly`'s **return value** (`if not _save_quietly(batch):`) so the rollback is
  reachable, and give the item a 30-second `retry_at` backoff so the same pass does not immediately
  retry it - which is what turned a rolled-back dispatch into a job failing for an unrelated reason.
- **Correction to the 0.6.22 entry below:** that release claimed the loop was changed from `continue`
  to `break`. The patch's anchor did not match and the change never landed; the code kept `continue`.
  The backoff above is the real fix for the behaviour that entry described.
- Evidence: `tests/_hub_dispatch_proof.py` - the `_PROOF` program from `tests/test_hub_jobs.py`,
  extracted so a test can sit beside its packaged-Hub fixture - now runs **11 tests**: the original ten
  plus `DispatchPersistenceRollbackTests`, which asserts the Mac is released, the item is `queued`, the
  attempt is given back, `error_code=DISPATCH_PERSIST_FAILED` and `retry_at` is in the future.
  `tests/test_hub_dispatch_proof_gate.py` runs it in its own process (its fixtures set `STUDIOHUB_*` at
  import and would otherwise rebind the data directory for every module loaded after it).

## 0.6.27 — 2026-09-19 · picker families accept the field every client sends

### Three models looked broken because of a field name

The worker has two names for the same thing: some model families ask for a `preset_speaker`, others
for a `voice`. Every client — the app, the gateway, the drive kit — sends `preset_speaker`, because
that is the field the app has always carried. So jobs for Kokoro, orpheus, VibeVoice and Voxtral were
refused with *"needs a preset voice"* while holding a perfectly good one under the other name. The
worker now accepts either, which fixes every client at once instead of teaching each of them a
per-family detail.

With that, **orpheus, VibeVoice and Voxtral generate audio** — verified by running real jobs.

### Technical notes

- **`_mlx_kwargs_voice_picker` read only `params["voice"]`**, while the app, the gateway and the kit
  smoke all send `preset_speaker` (the Qwen name). Jobs for kokoro-mlx, orpheus, vibevoice and
  voxtral-tts failed with "needs a preset voice" while carrying a valid voice under the other name -
  which is why three working models looked unable to run at all. The worker now accepts either, at
  the place that knows the difference; the family-native field still wins when both are present.
- Found by the adoption smoke test (`qualification/smoke_new_models.py`): with the correct field,
  orpheus, VibeVoice and Voxtral produced artifacts on the first attempt. Kokoro still fails for an
  unrelated reason - its phonemizer needs `espeak-ng`, which is not on this Mac nor bundled (the app
  ships `ffmpeg` only) - and F5-TTS cannot run because its engine is not packaged at all.
- Evidence: `tests/test_preset_voice_field_alias.py` (4 checks) - the alias is accepted, the native
  field wins, a missing voice still refuses with the actionable message, and an unknown VibeVoice
  voice is still rejected.

## 0.6.26 — 2026-09-19 · package rows state model vs dependency

### The Hub now says whether a package is a model or a dependency

The list of installed packages described a codec, a tokenizer and a real model identically: same
engine, same "files verified", same ready runtime. Anything reading that list had to guess which was
which — and one guess put a tokenizer in a voice picker. Each row now states it:

- `type: model` — something a person can choose
- `type: dependency` — a package that exists only because another model needs it
- `dependencies: [...]` — what that model requires

The fact comes from the catalogue itself, where every model already lists its companions, so nothing
downstream has to infer it.

### Technical notes

- **`/api/hub/model-packages` rows gained `type` (`model` | `dependency`) and `dependencies`.** A
  codec (`snac_24khz`), a tokenizer (`S3TokenizerV2`) and a real model all reported the same
  `engine=voice-mlx`, `state=files_verified`, `runtime_state=ready`, so a consumer reading that list
  could not tell them apart - and the gateway published five companions as clone-capable voice
  models to Story Studio as a result. `_dependency_package_ids(catalog)` collects every package that
  appears in another manifest's `dependencies`, and each row now states which it is, so no consumer
  has to infer it from membership of some other list.
- Evidence: `tests/test_hub_jobs.py` + `tests/test_model_packages.py` (42 tests) pass.

## 0.6.25 — 2026-09-19 · manifests for the 14 adopted cache models

### Fourteen more models, adopted from what was already on disk

The old launcher's cache is now described to Studio Hub: fourteen models, each with a manifest naming
its exact revision, every file it needs with its size and checksum, and the memory it requires. Five of
them also bring the companion files they depend on, so a model like Chatterbox or MOSS-TTS can finish
loading instead of sitting half-installed.

Nothing was downloaded. All of it was already on this Mac, and three of the models — Echo-TTS and both
Fish Audio S2 Pro builds — declare more memory than this Mac has. They are stored for a bigger Mac and
skipped here automatically: the copy step leaves behind anything that does not fit.

### Technical notes

- **19 manifests generated from the cache** by `qualification/generate_manifests_from_cache.py`:
  the 11 staged models that fit this 16 GB Mac plus the three giants (echo-tts-base 24 GB,
  fish-audio-s2-pro-8bit 24 GB, -bf16 32 GB), and the five companions those families require
  (`s3tokenizerv2`, `snac-24khz`, `fish-s1-dac-min`, `moss-audio-tokenizer-nano`, `umt5-base`).
  `dependencies` is what `model_packages.preflight` checks, which is why a MOSS-TTS import stayed
  `partial` until its audio tokenizer was registered.
- File selection honours the catalogue's `ignore_patterns`: F5-TTS is 1.35 GB of the 2.70 GB present,
  because the alternate checkpoints stay out. Total 39 GB, all already on disk — no downloads.
- Memory floors come from the catalogue's declared `min_unified_memory_gb` and are written to
  `qualification.minimum_unified_memory_gb`, the same field `copy_models_for_this_mac.py` reads, so
  the drive kit copies the giants only onto a Mac that can run them.
- Note on the import: the flat-folder importer refuses symlinked sources, and HuggingFace snapshots
  are symlink trees into `blobs/` (some nested or broken in that old cache), so all 19 imports were
  refused atomically and the library stayed at 15 packages. The link-based route the gateway used
  (`POST /api/imports`, `mode: "link"`) is the one that handles this layout.

## 0.6.24 — 2026-09-19 · the storage families and the memory verdict reach the app

### "This Mac cannot run that" is now something you see

The Models page shows what is actually on disk, grouped the way the worker groups it — a model together
with the companions it needs — and flags the ones this Mac cannot hold:

> Storage cache · 16 families on disk · 2 cannot run on this Mac
> ⚠️ Echo-TTS (MLX) needs 24 GB; this Mac has 16 GB
> ⚠️ Fish Audio S2 Pro (MLX) needs 32 GB; this Mac has 16 GB

That comes from the memory each model declares, not from its file size — the 6.7 GB 8-bit Fish model
is in a family whose declared floor is 32 GB, so size alone would have called it fine. This matters
most for models copied in from another install: they are present on disk and would otherwise look
runnable here.

### Technical notes

- **The app now reads `/studio/voice/api/model-storage`** (the payload the worker has always served,
  which no part of the native app rendered) and shows a **Storage cache** line above the model split:
  the family count, how many cannot run on this Mac, and one warning per family naming the declared
  floor and this Mac's memory. It sits above the split with the other notes, so it reads as context
  for what is installed.
- The fetch is `try?`-contained like the rest of the refresh since 0.6.18: a Studio that does not
  answer leaves the list empty and cannot disturb models, jobs or connections.
- Evidence: the native fixture serves a two-family storage payload and the API contract test asserts
  both families decode and that exactly one is flagged, carrying both numbers (`required_gb: 32`,
  `this_mac_gb: 16`). Build and both native gates pass. Never copied from a plan: the verdict itself
  was verified live against the real endpoint in 0.6.23 (Echo-TTS 24 GB, Fish Audio S2 Pro 32 GB).

## 0.6.23 — 2026-09-19 · the storage page reports a memory verdict per family

### The storage page can now say "this Mac cannot run that"

Every model in the catalogue already declares how much memory it needs. The storage page now joins
each family to its declared floor and reports it against this Mac's memory, so a model that cannot
run here says so instead of looking like any other download:

- fish-audio-s2-pro-bf16 — needs 32 GB, this Mac has 16 GB
- fish-audio-s2-pro-8bit and echo-tts-base — need 24 GB

That is measured, not guessed: file size would have called the 6.7 GB 8-bit model fine.

### Technical notes

- **`model_storage.inventory()` now attaches `memory: {required_gb, this_mac_gb, runs_here}` to each
  family**, joining the family's model items to the catalogue's declared `min_unified_memory_gb`. The
  floor belongs to the model, so the family takes the highest among its models; companions contribute
  none, and a family whose entries declare no floor reports `null` rather than a guess.
  Measured on this 16 GB Mac: `fish-audio-s2-pro-bf16` (32 GB), `fish-audio-s2-pro-8bit` (24 GB) and
  `echo-tts-base` (24 GB) are flagged `runs_here: false`.
- **Two bugs caught while landing it, both by the tests being wrong rather than the code:**
  (1) the first test called `inventory()` from the repository, where the cache resolves elsewhere, so
  it exercised **zero groups** and passed without touching the new code; (2) `_tts_models()` returns
  `ModelEntry` dataclasses, so the first implementation's `.get("min_unified_memory_gb")` would have
  raised `AttributeError` on every real request — invisible to that vacuous test. The join is now a
  unit-testable `_family_memory(items, tts_models, this_mac)` that reads dataclasses or mappings, and
  the tests drive it with fakes (6 checks that fail against the broken version), plus a live check
  that skips honestly when the process can see no cache.

## 0.6.22 — 2026-09-19 · a rolled-back dispatch retried the same Studio at once

### A rolled-back dispatch is left for the next pass

When a job cannot be written down as it is handed to a Mac, the Hub undoes it — releases the Mac and
puts the job back in the queue with its attempt returned. It then used to try that same Mac again
immediately, which fails the same way and produces a second, differently-broken attempt. It now
leaves the job for the next pass.

### Technical notes

- After the rollback added in 0.6.19, the loop `continue`d to the next candidate Studio - with a
  single Studio that meant retrying the machine whose write had just failed, in the same pass. The
  trace shows what that produced: the write fails, the Mac is released, the item is requeued, and then
  a second attempt runs the job and fails for an unrelated reason (`'input_path'`), which is exactly
  the "stuck job that fails for the wrong reason" shape this work set out to remove. It `break`s now,
  leaving the batch for the next pass.
- The rollback itself is verified by an instrumented live run of the real dispatch path
  (`/tmp/diag2.py`), which prints: `acquire_external_machine -> True`, `_save called: state=running`,
  `release_external_machine`, i.e. the Mac is given back and the item requeued rather than left
  running with no task. Its automated regression test is not landed yet (see the changelog entry for
  0.6.19): the harness needs the packaged modality, the monitor binding, a peer snapshot, a Studio
  that advertises the model, and the monitor's client, all of which are now written down.

## 0.6.21 — 2026-09-19 · unguarded bookkeeping writes aborted the dispatch pass

### A disk that filled up could stop the whole scheduler

While chasing something else I found that the Hub writes its own bookkeeping — which Studio was
tried, why it was refused, a note for you — with no protection. If one of those writes failed (a full
disk, an unreadable database), the failure escaped the scheduler entirely: **no other job could be
dispatched**, and what you would have seen is the Hub appearing to stall rather than anything saying
"this Mac cannot write". Those writes now fail on their own, log why, and the scheduler carries on
with the work it can still do.

This is a fix that came out of the last piece of unfinished work: I was trying to reproduce a
different write-failure bug and the trace pointed here instead. Both are the same family — a durable
write failing somewhere it was assumed to succeed.

### Technical notes

- **Five `_save(batch)` calls in the transcription dispatch loop were unwrapped** - the governor-note
  writes and the other bookkeeping the scheduler records as it goes. A raising `_save` there
  propagated out of `dispatch_once`, so a full disk or an unreadable database did not merely lose a
  note: the pass ended before any other job could be dispatched, and the failure appeared as a
  scheduler error rather than as a write failure. New `_save_quietly(batch) -> bool` logs the failure
  and returns False; the five bookkeeping writes use it. The lease-path write keeps its own
  try/except with the rollback from 0.6.19, because that one has state to undo.
- Found by instrumenting the reproduction of the 0.6.19 rollback: the trace showed `dispatch_once`
  raising from line 771 (a governor-note write) rather than reaching the lease at all.
- Evidence: `tests/test_dispatch_bookkeeping_writes.py` (3 checks) - a failing write is reported and
  survived, a good write still reports success, and every bookkeeping write in the loop goes through
  the helper. `tests/test_hub_jobs.py` still passes.

## 0.6.20 — 2026-09-19 · a Mac's health came from one arbitrary Studio

### A Mac's health is now all of its Studios, not whichever one is listed first

The Fleet page reports one row per Mac, and it was taking that Mac's health from the **first** Studio
it happened to list. So a Mac running Voice and Image, with **Image down**, read as healthy because
Voice was fine — and a Mac with one stubborn Studio could read as down while everything else on it
was working. Nobody is asking "is Voice up" when they look at that row; they are asking whether the
Mac is well.

It now reads **degraded** when part of the Mac is broken, **down** only when nothing on it answers,
and says which Studio is the problem:

> Subtitle is not answering

### Technical notes

- **`fleet_overview` iterated `monitor.registry`, skipped machines it had already seen, and reported
  the first studio's status as the machine's.** A Mac running Voice and Image with Image down read
  "up" if Voice came first, and read "down" while Voice was healthy if it did not. New module-level
  `machine_health(states)` summarises every Studio on that machine: `up` when all answer, `degraded`
  when some do, `down` only when none do, `unknown` when no status is known yet (a Hub that has not
  run its first poll must not report a Mac as sick), with a sentence naming the Studios that are not
  answering; the row also carries `studios: {total, up}`.
- Evidence: `tests/test_machine_health_summary.py` (6 checks) covers all five outcomes, that a Studio
  still starting does not make the Mac look down, and that the route no longer reads a single
  studio's status.

## 0.6.19 — 2026-09-19 · a failed dispatch write stranded the job and the Mac

### A job that could not be recorded no longer sticks as "running"

When the Hub hands a job to a Mac it writes that down first, and then starts watching the job. If that
write failed — a full disk, an unreadable database — the job was left reading as **running** with
nothing watching it: it could never finish, never fail, and never be retried, while the Mac stayed
reserved for it and read as busy. The only way out was restarting the Hub. Both are now rolled back —
the job returns to the queue, the Mac is released, and the next pass tries again — and it says so:

> A job could not be recorded when it was handed to a Mac, so it is queued again.

### Technical notes

- **`transcription_jobs`' dispatch loop leased the Mac, marked the item running in memory, then
  saved.** A raising `_save` (full disk, unreadable database) propagated out of the loop, leaving the
  item `running` with no task attached - it could never finish, fail or be retried - and the Mac
  leased to that job, reading as busy until the Hub restarted. The write is now wrapped: on failure
  `broker.release_external_machine` is called, the item returns to `queued` with its running fields
  cleared, the attempt it had just spent is given back, the studio is dropped from the pass's busy
  set, and the batch records why ("...so it is queued again").
- Evidence: `tests/test_hub_jobs.py` (7 tests) passes, including the dispatch paths it exercises. A
  dedicated regression test could **not** be placed reliably: the fixture constants it needs
  (`_WORKERS`) sit inside a dead region of that module (see below), so the test was withdrawn rather
  than left failing. This is the one fix today without its own test, and it is recorded as such.
- New finding, from trying to write that test: **`tests/test_hub_jobs.py` contains a large dead
  region.** Its AST shows only three top-level classes and two assignments while the text contains
  five `class` definitions and constants such as `_WORKERS` at line 39 - so some of that file's
  fixture code and at least one duplicate class definition never execute. Two of my edits this session
  landed inside it and silently did nothing until a test failed. That is the "tests that cannot fail"
  class of defect, and it is the next thing to fix.

## 0.6.18 — 2026-09-19 · the app re-read every historical job every two seconds

### The app stopped re-reading its entire job history every two seconds

Every two seconds the app asked the Hub for the full detail of **every job it had ever recorded** —
one request per job, over and over, for records that could not have changed. On a controller with
months of history that is the app working against itself, and against the Hub, all day.

A job that has finished is now read once and remembered. Before relying on that I checked the fear
that had held this back: that a job you retry could come back to life and be shown as permanently
finished. **The app has no retry button**, and the Hub's retry, remove and clear functions have **no
route** — nothing can re-open a finished job today. The states that *do* come back (an uncertain
transcript that gets re-queued) are not finished states, so they are never remembered. A test now
fails the moment a retry route is added, pointing at the cache, so this cannot rot into hiding a job
you restarted.

### Technical notes

- **`refresh()` fetched one job detail per batch, every two seconds, for ever.** History does not
  change, so `finishedJobCache` now remembers a job whose state is final (`done`, `cancelled`) and
  the poll reuses it; the cache is rebuilt each pass from the batches the Hub still lists, so it
  stays as small as the listing, and cleared on `disconnect()`.
- **The safety condition was checked, not assumed.** The reason this was deferred twice is that a job
  you retry could become live again and be shown as permanently finished. Traced: the app has no
  retry action (its only mention of the word is a message), and `broker.retry_batch`,
  `remove_batch` and `clear_terminal` have no HTTP route and no caller outside tests. The states that
  do come back - `uncertain`, re-queued by the Hub - are not final and are never cached.
- Evidence: the native fixture gained a job-detail read counter and asserts a finished job is not
  fetched on a second poll (and that the failed-detail path still reports, from an isolated store);
  `tests/test_job_detail_cache_is_safe.py` (5 checks) pins that only final states are cached, that a
  reconnect drops the cache, and **that no route can re-open a finished job** - so adding one fails
  the test with instructions rather than silently hiding a retried job. The fixture's unresolved-image
  case now uses its own batch id instead of flipping a finished job's state, which is what a retry
  would look like and cannot happen today.

## 0.6.17 — 2026-09-19 · the voice loader could run a different revision than the job recorded

### The model files loaded are now the ones the job says they are

When a voice model is loaded, the app can have more than one revision of it on disk: the one it
verified and the job records, and an older one left behind in the download cache. The loader used to
take whichever the filesystem happened to list first — so a job could run the **older** files while
writing the **newer** revision into its record. The record is what you'd trust when a result looks
wrong or needs reproducing, and it was describing files that were not used. The verified revision is
now loaded first, and when there is no verified package the newest cached one is chosen
deterministically instead of by chance.

### Technical notes

- **`GenerationManager._mlx_audio_snapshot_path` returned `candidates[0]`** from
  `models--<repo>/snapshots/`, and consulted `cache.package_snapshot` (the verified-package library,
  which resolves the manifest's exact revision) only when the cache had no `snapshots` folder at all.
  With both present, the arbitrary cache folder won: a Mac holding the verified revision *and* an
  older leftover could load the older files while the job wrote the verified revision into its record
  as immutable evidence - wrong evidence, and a result that cannot be reproduced from the record.
  The verified package is now resolved first, and the cache fallback sorts by modification time
  (newest first) instead of trusting filesystem order.
- Evidence: `tests/test_voice_snapshot_revision.py` (4 checks) - the verified revision wins over the
  cache, the cache fallback picks the newest and is stable across calls, a missing cache still names
  the fix, and a single revision needs no choice.

## 0.6.16 — 2026-09-19 · a refused credential looked like a switched-off Mac

### A Mac that was switched off, and a Mac that was told no

The Fleet page had one way of saying "no answer": unreachable. So a node that *replied* and refused
this controller — which happens when a node is re-joined with its own token, or a controller's fleet
token is replaced — looked exactly like a Mac that was switched off. That sends you to the network,
and to the wrong Mac, when the fix is a token. It now says so plainly:

> the node answered but rejected this controller's credential (HTTP 401) — re-join it with the
> current Fleet Token

and it is counted separately from the Macs that are simply not answering.

### Technical notes

- **`fleet_nodes.probe` treated every non-success answer as a generic error**, and the Fleet page's
  reading of a node that does not answer is "unreachable". A 401/403 means the opposite: the Mac is
  up and refused *this controller* - the state you get when a node was re-joined with its own token,
  or the controller's fleet token was replaced. The probe now returns `state="unauthorized"` with an
  actionable detail naming the status and the fix, and `inventory`'s summary counts it separately
  from `unreachable`, so it cannot hide inside a total.
- Evidence: `tests/test_fleet_credential_refusal.py` (5 checks) - a 401 and a 403 are their own state
  with the Fleet Token named, other failures stay errors, a healthy node still reads reachable, and
  the summary counts a refusal separately from a silent Mac.

## 0.6.15 — 2026-09-19 · the fleet inventory probed nodes one at a time

### The Fleet page no longer waits for every silent Mac in turn

Listing the joined Macs asked each one in sequence, and each silent one holds the line until it times
out. So a controller with several switched-off Macs took that many timeouts to answer a single page -
which the app reports as the Hub not answering at all. Every Mac is now asked at the same time, so the
wait is one timeout in total, however many are asleep.

### Technical notes

- **`fleet_nodes.inventory` awaited `probe` in a loop.** Each probe waits out `PROBE_TIMEOUT` for a
  silent node, so the page cost one timeout per switched-off Mac - ten of them meant ten timeouts
  before the app's own 30-second read gave up and called the Hub unresponsive. The probes now run
  under `asyncio.gather` with the order preserved; `probe` itself is unchanged and its per-node errors
  were already swallowed.
- Caught while doing it: the change needed `import asyncio`, which the module did not have - the
  syntax check passed and the call would have failed at runtime, so the module was imported and
  `inventory` called directly before trusting it.
- Evidence: `tests/test_fleet_inventory_concurrent.py` (3 checks) - four nodes that each take 0.15 s
  finish well inside half the sequential time, every probe starts before the first finishes, and the
  per-node state is still reported.

## 0.6.14 — 2026-09-19 · a cancel during publishing was overwritten by "done"

### Cancelling a Subtitle job now always cancels it

There was a small window — the moment a job finishes and the files are being written — where pressing
**Cancel** did nothing: the worker had already decided the job was finished, and it recorded "done"
over your cancel. The app then showed a completed job you had cancelled, with its files kept. The
window is the one you are most likely to hit, because it is exactly when a job *looks* finished. A
cancel that lands in it now wins, and the files written at that moment are removed with it.

### Technical notes

- **`SubtitleManager._run` checked the cancel event after decoding, then published artifacts and
  committed `state='done'`.** A cancel arriving between those two points - after the check, before
  the commit - was silently overwritten: the app showed a finished job the operator had cancelled,
  with artifacts kept, and the Hub's lifecycle treated it as completed work. The commit now re-checks
  the event inside the lock, marks the job cancelled, drops the artifact references, removes the
  files this call just wrote, and returns.
- Evidence: `tests/test_subtitle_cancel_wins.py` (3 checks) drives the real `_run` with a stub
  decoder and a `_publish` that sets the cancel event mid-write - the job ends cancelled, no
  artifacts are advertised and the written file is gone - plus two controls: an uncancelled job still
  finishes and keeps its file, and a cancel before decoding still cancels. Mutation-verified
  (removing the guard fails the first).

## 0.6.13 — 2026-09-19 · the download row drew a hardcoded zero

### A downloading model no longer looks stuck

The Downloads row drew a progress bar for anything in progress. For a model the Hub downloads, there
is no percentage to draw — the Hub hands that job to a separate process and only reports whether it
is running, and the app filled the gap with a zero. So the bar sat at **0%** for the entire download,
and the size read **0 bytes** for a 12 GB model: the row looked broken while everything was fine.

It now shows a spinner and the model's own size — *"Downloading in the background · 12.4 GB model"* —
which is the same figure the Models page shows. Downloads whose progress the app *does* know (the ones
a Studio reports) still show their real bar.

### Technical notes

- **The Downloads row drew `ProgressView(value: percent)` unconditionally** for an active download.
  `model_downloads` runs the downloader as a child process and records only `{"id", "package_id",
  "state", "started_at", "pid"}` - there are no byte counts anywhere in the Hub - and the app filled
  the gap with `percent: 0, downloadedBytes: 0, totalBytes: 0`, so a 12 GB model read "0 bytes" and
  the bar never moved for the whole download. The row now draws a bar only when there are real
  numbers (`percent > 0 || totalBytes > 0`) and otherwise shows a spinner with the model's own size,
  looked up from the catalog - the same figure the Models page displays.
- Note: the Models page already showed a real size per model (`Estimated size`); the misleading
  figure was in the transfers list.
- Evidence: `tests/test_download_progress_honesty.py` (4 checks) - the unconditional bar is gone, a
  bar is still drawn when numbers exist, the caption names the model's size, and the Hub's run record
  genuinely carries no progress (so the honest thing is a caption, not a percentage).

## 0.6.12 — 2026-09-19 · `cleanup_expired` had no caller

### The duplicate voice clips were never cleaned up

When a voice is sent to a Studio to clone from, the Hub makes a **duplicate working copy** of that one
audio clip so the worker can read it. Those copies were given a 24-hour deadline, and the deadline was
only ever used to *refuse* the copy afterwards — nothing deleted it. So every duplicate ever made
stayed on disk for the life of the Mac. They are now swept up with everything else.

Your voices are untouched by this and always were: a voice in your library, and a voice shared across
the fleet, is permanent. This is only about the throwaway duplicate of a clip, which the Hub can make
again in a second.

### Technical notes

- **`execution_assets.cleanup_expired` existed and was called from nowhere.** Staged voice-reference
  copies carry an `expires_at` (24 h default, 48 h maximum), and the deadline was only honoured by
  `resolve_voice_reference`, which *refuses* an expired asset - so the file stayed. Every duplicate
  clip the Hub ever made for a worker was retained for the life of the Mac. `job_storage.
  _enforce_budget` now calls it alongside `transcription_jobs.cleanup`, so the five-minute sweep
  deletes them. The library and shared voices are unaffected: they are not staged assets.
- Evidence: `tests/test_execution_asset_purge.py` (4 checks) - an expired copy is deleted, a live one
  is kept, a folder whose metadata cannot say when it expires is cleared out (the store's existing
  behaviour, now pinned so the sweep cannot quietly start keeping junk), and the sweep is what calls
  it.

## 0.6.11 — 2026-09-19 · the voice transcribe upload was unbounded in memory

### A recording read into memory whole, with no ceiling

Transcribing a recording you upload — as opposed to one this Mac already generated — read the entire
file into the worker's memory before writing it to a temporary file. There was no limit on it at all:
a large enough upload, or a direct caller that ignores the app, could take the machine down by
exhausting memory while it was holding a model. It is now streamed to disk a piece at a time, with the
same per-file ceiling the Hub uses, and a refused upload costs one piece rather than the whole file.

### Technical notes

- **`main.py:1602` did `data = await file.read()`** in the transcribe-by-upload handler: the whole
  recording in memory before it reached the temporary file, with no cap on its size. (The audit said
  "voice upload endpoints apply the 25 MB limit after buffering" - that half is wrong: the
  reference-audio path already reads `reference_audio.MAX_BYTES + 1` at `main.py:1316`. Verified
  against the source before changing anything, which is why only this route was touched.) Now
  streamed in 1 MiB pieces to the temp file with `MAX_TRANSCRIBE_UPLOAD_BYTES` (matching the Hub's
  2 GiB per-file limit), the temp file removed on every failure path, and the existing empty-upload
  and read-failure responses kept. Gated on the full suite rather than on a unit test of a helper -
  the lesson from 0.6.9, where a helper test passed while the route was broken.

## 0.6.10 — 2026-09-19 · fix for a regression in 0.6.9

### A correction to 0.6.9

0.6.9 changed how Subtitle reads an upload so that an oversized file is refused as it arrives rather
than after it is in memory. That change referred to the size limit on the wrong object, which broke
submitting **any** Subtitle job — it would have failed immediately, and the test suite caught it
before anything was handed to you.

The limit is now read from where it is actually defined, and all 57 Subtitle tests pass. Nothing else
in 0.6.9 changed. If you installed 0.6.9 in the last few minutes, this replaces it; every other
release today is unaffected.

### Technical notes

- **0.6.9 referenced `manager.MAX_INPUT_BYTES`**, but `manager` in that module is the *instance* and
  the constant is a module-level name in `manager.py` - so every Subtitle upload raised
  `AttributeError: 'SubtitleManager' object has no attribute 'MAX_INPUT_BYTES'`. Caught by the full
  suite before it reached the owner (`tests/test_subtitle_standalone.py`: 4 errors, all on upload).
  The handler now imports and uses the module constant.
- The lesson worth recording: the new unit checks passed against a fake upload while the real
  endpoint path was broken, because they exercised the helper rather than the route. The
  route-level tests in `test_subtitle_standalone.py` are what caught it, which is why they are the
  gate for anything touching a handler's collaborators.

## 0.6.9 — 2026-09-19 · the Subtitle upload limit was applied after buffering

### A huge upload can no longer exhaust the Mac's memory

Subtitle's per-file limit is 2 GB. The worker read the **entire** upload into memory first and
checked the size afterwards — so a file that was far too large was held in RAM before being refused,
on a Mac that is usually also holding a model in unified memory. That is an out-of-memory kill rather
than a clean refusal. The body is now read in pieces and refused the moment it passes the limit, so
the cost of refusing is one piece instead of the whole file.

### Technical notes

- **`data = await file.read()` in both Subtitle upload handlers** returned the whole body before
  `manager.MAX_INPUT_BYTES` (2 GiB) was checked in `manager.store_input`. An oversized upload was
  therefore buffered in the worker process first; on a Mac holding a model in unified memory that is
  an OOM kill rather than a 413. New `read_upload_limited(file, limit)` reads in 1 MiB pieces and
  raises 413 at the first byte past the limit; both handlers use it.
  Evidence: `tests/test_subtitle_upload_limit.py` (5 checks) - a body within the limit comes back
  whole, an oversized one is refused with 413, **the refusal stops reading** (four reads of a
  thousand-piece body, the rest untouched), an empty body is fine, and no handler reads the body
  directly any more. Mutation-verified (reading the whole body per call fails three).
- Still open from the audit, and unchanged by this: a *legitimate* upload up to the 2 GiB limit is
  still held in memory before being written to the store, which wants the store API to accept a
  stream rather than bytes.

## 0.6.8 — 2026-09-19 · an alert failure aborted the health poll

### An alert can no longer take the health check down with it

The Hub watches each Studio every few seconds and that watch is what tells the rest of the app
whether a Studio is alive — including whether a job may run at all. Sending a "Studio went down"
alert happened in the middle of that watch, unguarded: if the alert could not be sent (a webhook that
does not answer, an alert log that cannot be written), the exception ended the whole health pass. The
studios kept reporting whatever they last said while the app showed it as current, so a Mac could be
shown as ready while it was gone, or the other way round. Alerts now fail on their own.

### Technical notes

- **`_note_transition` and `_note_restart_health` called `alerts.emit` unguarded**, inside the poll
  path. An unreachable webhook or an unwritable alert log raised out of the pass, so `self.status`
  kept its previous values for every Studio while the app presented them as current - and since
  0.6.3 the admission guard reads exactly that status to decide whether a job can run. Both helpers
  now use `self._alert`, which swallows the failure; the alerting module still raises for direct
  callers, so a broken alert is not silently hidden from anything that asks it directly.
  Evidence: `tests/test_monitor_alert_isolation.py` (6 checks) - a down transition, a recovery and a
  restart alert each survive a raising `emit`, a working `emit` is still called with the right kind,
  startup/steady-state transitions stay silent, and only the guarded helper may emit directly.

## 0.6.7 — 2026-09-19 · the identity file was written in place

### A Mac can no longer forget who it is

The small file that holds this Mac's identity — its name, its role, the settings that keep it
contained — was written by overwriting it in place. Overwriting starts by emptying the file, so a
crash, a forced quit, or a disk that filled at the wrong moment left it half-written; the next start
reads that as unreadable and falls back to the defaults, and the Mac quietly lost its identity. It is
now written beside itself and swapped in one step, so either the new settings are there or the old
ones still are, and the file is private to your account (it holds identity, not just preferences).

### Technical notes

- **`control_plane` wrote `controller_settings.json` with `write_text`**, which truncates first: a
  crash, kill or full disk between the truncate and the write left a partial file, and the loader
  treats unreadable as "no settings" - this Mac's identity and containment settings silently returned
  to defaults. Same for `database_url.txt`. New `_write_private_file(path, payload)`: temporary file
  in the same directory, `flush` + `fsync`, mode 0600, then `os.replace`. The rename either happened
  or it did not; there is no third state.
  Evidence: `tests/test_atomic_settings_write.py` (4 checks) - a failing `os.replace` leaves the
  previous content byte-identical and still parseable, a successful write is whole and 0600, no
  temporary file is left behind, and neither file is written directly any more. Mutation-verified
  (a direct write fails two of them).

## 0.6.6 — 2026-09-19 · a cancel Event per queued image job leaked

### A slow leak from cancelled image jobs

Cancelling an image job **while it was still waiting** left a small piece of bookkeeping behind, and
nothing ever removed it — the app runs for weeks, so a Mac that had cancelled a few hundred queued
image jobs held all of them in memory for as long as it stayed open. Cancelling a job now cleans up
after itself, whether it was already generating or still in the queue.

### Technical notes

- **`GenerationManager._run` returned without releasing the cancel event.** When a job was cancelled
  while queued, the job's thread reached the top of `_run`, saw the event set, marked the job
  cancelled and returned - skipping the only two paths that remove the entry from `cancel_events`
  (a failed running-state persist, and the `finally` of the inference block, both of which come
  later). One `threading.Event` was therefore retained for every such cancellation, for the life of
  the process. The early-cancel path releases it now.
  Evidence: `tests/test_image_cancel_event_release.py` (3 checks) drives the real method - a
  cancelled-while-queued job, twenty-five of them in a row leaving the dictionary empty, and that
  `cancel()` still marks a queued job cancelled. Mutation-verified: removing the release fails two.

## 0.6.5 — 2026-09-19 · the seed catalog route was shadowed

### The ready-made voices were unreachable

**Voices → Ready-Made was always empty, and nothing said why.** The route that lists the curated
clips was registered *after* the route that fetches one voice by its id, and the framework picks the
first one that matches — so asking for the catalog was answered by "fetch the voice called
`seed-catalog`", which does not exist. The list was never wrong; it was never asked. Fixed, and the
test fails if the order is ever reversed again (checked by putting it back).

### Technical notes

- **`GET /api/voices/seed-catalog` was declared after `GET /api/voices/{voice_id}`.** FastAPI matches
  in declaration order, so the parameterised route answered first and the single-voice handler ran
  with `voice_id="seed-catalog"` - "voice not found". The ready-made catalog (`voices.seed_catalog`)
  was therefore unreachable from the app and the tab was permanently empty. The literal route is now
  registered before the parameterised one.
  Evidence: `tests/test_seed_catalog_route.py` (4 checks) asserts the declaration order, resolves the
  URL through the router (so it names the handler that actually answers), checks a single-voice URL
  still reaches the voice handler, and calls the handler. Mutation-verified: declaring the route last
  again fails two of them.

## 0.6.4 — 2026-09-19 · the admission sweep no longer runs in the scheduler's inner loop

### The Hub stops re-reading every file several times a second

Before accepting a job the Hub asks whether the storage rule allows it — and it answered that
question by **walking and hashing every managed output folder**. It asked it for every waiting job,
on every scheduling pass, several times a second, on the thread that answers the app. On a Mac with
the full model library and a busy history that is the Hub stalling itself, and it is why the app
could feel unresponsive while nothing was actually wrong.

The answer is now remembered for a few seconds, so a burst of scheduling reuses one reading. Nothing
about enforcement changes: files are still deleted on their own timer and whenever you ask for it,
and each machine's own limits are still asked about separately.

### Technical notes

- **`output_storage.admission` calls `enforce`**, which walks and hashes the managed output roots.
  `_submission_readiness` calls admission for every queued item on every dispatch pass - several
  times a second while work waits - synchronously on the event loop, so a Mac with a full library and
  a busy history stalled its own Hub (and the app's polls with it). Admission now answers from
  `_ADMISSION_SNAPSHOT`, a 10-second per-policy cache, deep-copied in and out so a caller annotating
  its refusal cannot corrupt a later answer, with a 64-entry bound so a fleet's per-node policies do
  not evict one another. Enforcement is unchanged: the background timer and every operator action
  still run it.
  Evidence: `tests/test_storage_admission_cache.py` (5 checks) - one sweep per burst, per-policy
  separation, the refusal still computed from the real numbers, copies handed out, and the snapshot
  expiring. All 27 tests in the modules that touch output storage still pass.

## 0.6.3 — 2026-09-19 · no Studio, no acceptance

### A job that nothing can run is refused, with the reason

If a Studio was not running — Voice, Image or Subtitle — a job sent to the Hub was still **accepted**.
The checks before acceptance covered the model's files, its runtime, your version of macOS, the chip
and the storage budget, and stopped there; nothing asked whether anything was running to do the work.
The job then sat in the queue for ever, shown in the app as accepted work, because dispatch needs a
live Studio and there was none. It is refused now, by name:

> no voice Studio is running on this Mac

or, when a Studio has stopped answering:

> the voice Studio is not answering; restart the local services

A Studio that is registered but has not been health-checked yet (the first moments after the app
starts) still accepts work, so nothing is refused while the checks are warming up. Another machine's
Studio still counts, so a controller with no local Studio can keep sending work to its nodes.

### Technical notes

- **The submission guard never asked whether a Studio was running.** `_submission_readiness` checked
  the packaged model gates and the storage budget; with no live Voice worker, `_eligible_studios`
  found nothing to dispatch to, so the batch sat in `queued` for ever while the app showed accepted
  work. There is no deregistration either - a dead worker stays in `monitor.registry` - so liveness
  is `monitor.status[id]["status"]`. The guard now refuses with `STUDIO_NOT_RUNNING` when the
  modality has no worker at all, or when every worker for it is explicitly not answering
  (`unknown` is left alone, so a Hub that has not run a health check yet does not refuse work at
  startup). Remote studios count, so a controller can still route to its nodes.
  `tests/test_admission_requires_a_studio.py` (5 checks) pins each branch, including that another
  modality's Studio does not count and that a node's does.
- This is the Hub-side half of the reason work in 0.5.7 (the app already named the reason; the Hub
  went on accepting the job).

## 0.6.2 — 2026-09-19 · mp4/mov audio extraction

### Video files work now

**You can hand Subtitle a screen recording.** The app offered .mp4 and .mov and then always failed:
the transcription decoder reads audio through a library that cannot open those containers, so the
file was accepted, queued, dispatched, and produced nothing — with no way to tell that the file was
the reason. The audio track is now extracted with the ffmpeg that already ships in the app, and the
recording is transcribed like any other file. A file whose audio genuinely cannot be read says so,
in the job, instead of failing silently.

That completes your three answers from the audit — storage now follows your 50 GB / 30 day rule, an
uncertain transcription recovers on its own after 15 minutes instead of holding its Mac, and video
files are transcribed.

### Technical notes

- **Subtitle accepted .mp4/.mov and always failed.** Both the upload validation
  (`manager.ALLOWED_EXTENSIONS`) and the Hub (`transcription_jobs`) allow them; `engine.transcribe`
  handed the path to a Whisper decoder that reads audio through miniaudio, which does not open those
  containers, so a screen recording was accepted, dispatched and produced nothing. New
  `engine.decodable_audio(path)` context manager + `find_ffmpeg(voice_app_dir)`: a video container's
  audio track is extracted by the bundled ffmpeg (mono, 16 kHz, WAV, `-vn`) into a temporary
  directory that is removed afterwards, and ordinary audio passes straight through. ffmpeg is located
  the same way the Voice service does it - `STUDIOHUB_TOOLS_DIR` (set by the packaged worker, see
  0.5.8), then the Voice app's tools folder, then PATH - and a genuinely missing ffmpeg raises a
  message naming the file and the reason instead of a decoder error. The duration probe and the
  model both read the extracted file, so the timing clamp still applies.
  `tests/test_subtitle_video_audio.py` (5 checks, mutation-verified: disabling the extraction fails
  four).
- This closes the owner's three audit decisions: storage enforcement (0.6.1), uncertain-transcription
  recovery (0.6.1) and video input (0.6.2).

## 0.6.1 — 2026-09-19 · the owner's storage and recovery rules

### Your three rules, two of them built

**Storage: 50 GB or 30 days, whichever comes first — now actually enforced.** The rule existed in the
app all along, at exactly those numbers, and was switched off in the version that ships: the cleanup
that runs it was only started in development. So a Mac left running kept every recording, every
transcript and every generated file until its disk filled and it began refusing work it could not
free. It now runs every five minutes, for every studio's files, and the saved recordings and
artifacts are removed as the rule says. Nothing disappears from **Jobs** — only the files.

**An uncertain transcription no longer holds its Mac.** When a response is lost, the Hub cannot tell
whether the worker is still transcribing, so it fences the item — and nothing ever released it: the
retry, remove and clear paths all refused it, and the Mac that held it was excluded from image, voice
*and* subtitle work, permanently. Now it is re-queued automatically after **15 minutes** and
transcribed again from the original recording, which is what you asked for. Transcripts are cheap to
repeat; a Mac out of the fleet is not.

**Video files: next release.** You approved extracting the audio track with the bundled ffmpeg, so
.mp4 and .mov work instead of always failing. That is a change to the subtitle worker's decoder path
rather than a switch, so it goes in next with its own test rather than at the end of a long day.

### Technical notes

- **The storage budget (50 GB / 30 days) was enforced only in development.** `job_storage.
  enforce_budget` - the shared sweep the runtime contract says supersedes the legacy timer - had no
  caller at all, and `transcription_jobs.start_dispatcher` starts its own cleanup loop only
  `if not _packaged()`. Nothing ever deleted an uploaded recording or a transcript in the shipped
  app, and the Hub's own storage figures never counted them. New `job_storage.budget_loop(interval)`
  + `sweep_once()`, started by the Hub's lifespan at `standalone_config.cleanup_interval_seconds()`
  (300 s by default, honouring the operator's setting) and cancelled on shutdown; the sweep runs off
  the event loop because it walks and deletes files. `tests/test_storage_budget_loop.py` (4 checks)
  pins the pass, the timer, that a failing sweep cannot kill the loop, and that the policy constants
  are the owner's numbers.
- **An uncertain transcription removed its machine from every lane, permanently.**
  `_mark_uncertain` sets `manual_action_required`; `retry_batch` skips such an item; `remove_batch`
  and `clear_terminal` refuse a non-terminal batch; no route exposes any of the three; and
  `_eligible_studios` excludes the machine for image, voice and subtitle alike. New
  `UNCERTAIN_RECOVERY_SECONDS = 900` + `recover_stale_uncertain(now)`, called at the top of
  `dispatch_once`: a stale fence is re-queued, its partial output deleted, the flag cleared and the
  reason written to the batch note. The busy-check comment now records that a fence is temporary by
  design: transcription is cheap enough to repeat. `tests/test_uncertain_recovery.py` (4 checks).

## 0.6.0 — 2026-09-19 · stale timings and unbounded memory

### Two more from the audit, both silent

- **Correcting a voice's words left the old timings behind.** A long reference clip needs its
  transcript *and* the timings that say which part of the clip each word is in. Editing the words in
  **Voices** rewrote the transcript and kept the timings measured from the old words, so a clone then
  chose its window from timings that no longer matched the audio — a wrong-sounding result with
  nothing on screen to explain it. Correcting the words now drops the timings; **Transcribe Clip**
  measures them again. Renaming a voice, or saving without changing the words, leaves them alone.
- **The Hub kept every job it had ever seen in memory.** Not on disk — those records are kept — but
  the in-memory copy a running job is polled through was never cleared, so a controller left running
  for weeks grew without bound and every status read walked all of it. Finished jobs now leave memory
  once they are a quarter of an hour old, keeping the twenty most recent (they are what the
  "about N seconds" estimate is measured from). Nothing in **Jobs** changes: the listing reads the
  saved record as well.

### Technical notes

- **An edited transcript kept timings measured from the previous words.** `voices.update()` rewrote
  or removed `transcript.txt` and never touched `transcript.timed.json`, so the aligned timings a
  cloning model uses to pick its window described different words - the failure mode is a
  wrong-sounding clone with nothing to explain it. Editing the words (or clearing them) now removes
  the timed file; an unchanged transcript and every other field leave it alone. Four checks in
  `tests/test_voice_timings_invalidation.py`, mutation-verified (removing the invalidation fails two).
- **`broker.batches` was never pruned.** It holds every batch the process has been told about; the
  durable record is the ledger and the listings merge both, so the in-memory copy of a finished job
  is pure cost once it is old. `evict_finished_batches(keep_recent=20, older_than_s=900)` drops them,
  called once a minute from the dispatch loop; the most recent twenty stay because the per-model ETA
  is measured from them. Tested against a long-finished, a just-finished, a running and a half-done
  batch so a running job can never be evicted.
- Note on the test file: `tests/test_hub_jobs.py` has a region whose indentation made an inserted
  class land inside an existing block - the new class is at the end of the file now, and the module's
  seven tests run.

## 0.5.9 — 2026-09-19 · one failure is not the whole screen

### The app no longer gives up on the whole screen

Two more from the audit, both about one small failure looking like a big one.

- **One unreadable job used to blank everything.** The app refreshes eight things every two seconds,
  and if any one of them failed — a dead Subtitle worker, a finished download the Hub has forgotten,
  one job whose record will not load — it abandoned the entire refresh and marked the Hub
  unreachable. The models, the jobs, the voices and the connections all went to their empty states
  with no explanation, and only pressing something else brought them back. Each piece now stands on
  its own: the Hub is only "unreachable" if the Hub itself does not answer, and anything that could
  not be read is said in one line at the top.
- **A damaged file of the app's own stopped it connecting at all.** If one of the app's saved files
  in Application Support was truncated or in an unexpected shape, starting the app cleared the Hub
  address and left every page on "Choose where your models live" — and it never recovered, because
  the same file was read again on every attempt. The only way out was to find and delete it in
  Finder. A file that will not parse is now set aside (nothing is lost, it is renamed with
  `.corrupt` at the end) and the app starts without it, saying what it did.

### Technical notes

- **`refresh()` aborted on any single sub-request.** Eight reads per two-second poll were awaited as
  one tuple, and each batch detail and each Hub download run was read with a throwing `try` inside
  the loop, so one 500 - a pruned job, a dead Subtitle worker, a forgotten download - threw out of
  the whole function, and the catch set `isReachable = false`. Because the poll passes
  `reportErrors: false`, it also showed no banner: the models, jobs, voices and connections all
  emptied and nothing said why. The studio-level reads are now optional (with defaults), and a batch
  detail or download that fails is skipped and counted, with the count shown in one line; only the
  catalog and the package list - the Hub itself - can mark it unreachable.
- **A corrupt file of the app's own blocked `connect()`.** `loadState`, `loadPending*` and
  `loadHubDownloadRuns` were throwing calls inside the same `do` as the Hub handshake, so a
  truncated `native-workspaces.json` cleared `baseURL`/`hubToken` and set `isReachable = false`; the
  file was then re-read on every reconnect, so the app could not recover without finding it in
  Finder. They are best-effort now: an unreadable file is renamed with a `.corrupt` suffix (kept, not
  deleted) and named in `startupNote`, which the main banner shows and can dismiss.
- Evidence: the native fixture gained one unreadable batch detail; the test asserts `isReachable`
  stays true, the readable jobs survive and the skip is reported. A second assertion writes invalid
  JSON into the app's own state file and asserts the app still connects, keeps the file as
  `.corrupt`, and says so. Both were checked by mutation: reverting the best-effort load fails the
  test with "a damaged workspace file must not stop the app connecting".

## 0.5.8 — 2026-09-19 · first batch from a full audit

### Fixes found by auditing the whole app

I went through every part of the app looking for things that are wrong rather than things that look
untidy, and checked each one against the code before touching it. This release is the first batch.

- **A restarted node never told its controller where it was.** The automatic re-join — the thing that
  makes a group survive a restart — silently did nothing on every Mac: the code that re-joins asks
  the Hub what kind of credential this Mac holds, and on the startup path nobody had asked the Hub
  yet, so it always saw "nothing" and stopped. Now fixed, and the test that used to hide it fails
  without the fix.
- **A voice you removed could not be added again.** Removing a shared voice leaves a marker so every
  node drops it, and that marker also refused the voice when it was pushed again — so a re-add
  reported success and installed nowhere. Pushing a voice again now clears the marker, which is what
  pushing it again means.
- **Re-pushing a voice could wipe its words and timings.** The gateway re-pushes a voice whenever it
  is edited, and a push that did not repeat the transcript was treated as "clear it" — which would
  have removed the transcript and the timing from every controller and then every node. A field that
  is not mentioned is now kept; sending an empty one still clears it.
- **Updating the app could leave a Mac with no app at all.** The installer deleted the working copy
  before copying the new one, so cancelling the password prompt left nothing behind. It now copies
  beside the old one and swaps only after the copy is complete and has a runtime in it.
- **Subtitle could not find ffprobe**, so it could not read the duration of a recording — the check
  that keeps transcript timings inside the file was skipped for m4a, aac and webm. The voice worker
  was told where the bundled tools are; the subtitle worker was not.
- **The startup diagnostic always printed a false error**: it passed a flag the Hub does not accept,
  so "see the real error" showed an argument error instead.
- **A test that could not fail.** The test that was supposed to protect the voice-reference key only
  matched a comment in the app's own source; I checked by breaking the key deliberately and the test
  still passed. It now fails, as it should.

### Technical notes

A full audit of every component (hub backend, the three workers, the native shell, the kit and the
tests), with each finding verified against the source by a second pass before it was acted on.

- **The automatic fleet re-join never ran.** `StudioHub.start` called `rejoinControllerIfNeeded()`,
  which guards on `controllerIdentity?.settings.fleetCredentialOrigin`, and nothing on the startup
  path had called `refreshConnection()` — so the guard saw nil and returned on every Mac. The test
  passed only because it refreshed the identity by hand first. The launch step is now one function
  (`adoptStartupMembership`) that both the app and the test call, and the test fails without the
  identity fetch (verified by mutation).
- **A removed shared voice could never be re-added.** `upsert` recreated the voice (201) but the
  tombstone survived, and `start_sync` refuses any voice with a tombstone, so `reconcile_once`
  deleted it from the targets again. `upsert` now clears the tombstone for the id it is re-creating.
- **A metadata-only push cleared the transcript and its timings.** The route defaulted absent
  `transcript`/`transcript_segments` to `""`, and `upsert` wrote a full replacement, so a rename
  wiped the words and timings everywhere. Absent is now `None` = keep; `""` still clears.
- **`install_app.command` deleted the installed app before copying the new one**, so a cancelled
  password prompt or a full disk left the Mac with nothing. It copies to a staging folder in the
  target, checks the runtime there, and only then swaps.
- **`packaging/subtitle_worker.py` never set `STUDIOHUB_TOOLS_DIR`**, so `find_executable('ffprobe')`
  searched `Resources/voice/tools`, which the bundle does not contain; the media-duration guard was
  silently off for m4a/aac/webm. The voice worker set it all along.
- **`qualification/studiohub-start.sh` passed `--listen-host`** to a worker that defines `--host`, so
  the runbook's "see the real error" step printed an argparse error instead.
- **`WireEncodingTests` could not fail**: it searched the app's raw source plus quoted strings, and an
  app *comment* naming the Hub's key satisfied it (the Hub's own key is written in backticks, so the
  regex found nothing). A hand-written `CodingKeys` mapping to a wrong key - the exact mistake the
  file exists to prevent - left all three tests green. It now strips comments, forbids the literal in
  the app, and forbids `CodingKeys`; verified by mutation.

Still open from the audit, with the owner for decisions or queued next: subtitle retention (nothing
ever deletes an upload or an artifact, and the packaged Hub's cleanup loop is off), .mp4/.mov
accepted by Subtitle and then refused by the decoder, an uncertain Subtitle item that holds its Mac
out of every lane forever, finished broker batches never evicted, the output-storage sweep hashing on
the event loop, worker liveness missing from readiness, and the app's refresh aborting everything on
one failed sub-request.

## 0.5.7 — 2026-09-19 · a refusal with no reason

### A model that cannot run now says why

Somewhere in the app a model would just read **unavailable**, and nothing said what was wrong — so
the only way to guess was to wonder about memory. The Hub knows exactly which of five things is
missing, and checks all of them before it will accept a job: the model's **files**, its **runtime**,
your **version of macOS**, the **chip**, and whether its **Studio is running**. It has always sent
all five. The app was reading one of them and throwing the rest away.

Each reason now appears in orange, where you are looking: under the model list on Audio, Image and
Subtitle, on the Generate button's own explanation, and on the **Models** page. For example:

- *This Mac is on macOS 14.5; this model needs 15.0 or later.*
- *This Mac does not have enough memory for this model.*
- *Its files are not verified. Open Models and press Verify Files.*
- *Its Studio is not running. Restart the local services.*
- *This model needs an Apple Silicon Mac.*

A model that cannot run is also marked in the list itself (*"… — not available here"*) so it is
obvious before you pick it, rather than after you press Generate.

### Technical notes

- **The gates were sent and discarded.** `/api/hub/model-packages` returns, per model,
  `files_state`, `runtime_state`, `os_state`, `chip_state`, `worker_state`, `minimum_macos`,
  `macos_version`, `dispatch_state` and a `reason` when there is one. `HubModelPackage` decoded
  `package_id`, `model_id`, `state` and `dispatch_state` - so the app could tell that a model was
  unavailable and never which of the five independent gates was shut. The owner reported it as "just
  shows unavailable with no proper reason" and guessed at RAM pressure, which is exactly what a UI
  forces an operator to do when it withholds the reason it already has.
- `HubModelPackage` now decodes every gate, and one `blockReason` turns the first shut gate into a
  sentence: files not verified, runtime unavailable in this build, macOS floor (naming both
  versions), Apple Silicon required, studio not running, or the memory tier when the catalog knows it
  (`memory_warning` / `memory_eligible == false`, checked first because it is the one gate an
  operator cannot fix by clicking).
- Readiness is no longer computed by hand in three places with three different rules: the audio,
  subtitle and image mappers all ask the same function, so a model cannot be "ready" in the picker
  and refused by the Hub.
- It is shown in the generator's model list (a `— not available here` marker), under the selected
  model, in the Generate button's explanation, and on the Models page. Evidence: the native fixture
  now serves a package gated on macOS and asserts the sentence names both versions, that the picker
  marks it, and that a healthy model carries no reason; `tests/test_model_reasons.py` (6 checks)
  covers the decode, every gate's sentence, the precedence of the memory reason, and the three places
  it is shown.

## 0.5.6 — 2026-09-19 · naming a Mac, and the restart nobody knew about

### Name your Mac, and one button for settings that need a restart

**You can name this Mac now.** Connections → **Name** → *Rename*: that is what this Mac is called in
the fleet, in Jobs and to the gateway. It is a label, not an identity — renaming it never changes
routing, and never affects a node that has already joined.

**Changing the role, the network addresses or the port needs the services restarted**, and the
controls are switched off until they are — which quietly expected you to know to stop the services,
change the setting, and start them again. Those settings now say so, right above them, with one
button:

> ⟳ This change takes effect when the local services restart.
> **Restart Services** — they stop and start again on their own.

The role picker also explains itself in plain words: **Controller** runs its own group and sends
work to nodes, **Node** is sent work by a controller, **Standalone** is a Mac on its own.

### Technical notes

- **The name was never editable.** `registry.set_label` has written `machine_labels.json` since the
  beginning and `machine_name` - what every other Mac and the gateway read - comes from it, but no
  route and no control exposed it, so the only name an operator could have was the hostname. New:
  `POST /api/hub/machine-label` (Hub token only; a fleet credential is refused, checked against a
  live Hub), and a **Name** field with a Rename button in Connections. Empty clears back to the
  hostname. It is a label, never an identity: routing and fleet membership do not read it.
- **The restart was implied, not stated.** Role, network addresses and port are read when the Hub
  starts, so their controls are disabled while it runs - leaving a new operator to discover the
  stop/change/start dance. `StudioController.restartServices()` now stops and starts again in one
  press (`restartAfterStop`, honoured in `workerExited`), a notice appears above those controls while
  a change is waiting, and `hasPendingRestartSetting` compares the port the Hub actually bound with
  the saved one so the notice tells the truth rather than remembering an edit. The role picker also
  gained a line saying what Controller / Node / Standalone mean.
- Evidence: `tests/test_machine_name_and_restart.py` (6 checks) - including a live Hub rename that is
  read back through `/api/hub/controller`, a cleared name falling back to the hostname, and a fleet
  credential being refused. Both halves were rendered before shipping with the new
  `STUDIOHUB_RENDER_RESTART=1` mode, which shows the notice that only appears while services run.

## 0.5.5 — 2026-09-19 · how long a generation took, which the app never said

### You can see how long a generation takes

Every finished job now says how long it took, in the generator and in **Jobs**: *"Completed · took
52.2 s"*, or *"took 1 min 28 s"* once it is long enough to read that way. A generation still running
says *"running 12 s"* instead, so there is a clock either way.

This is a different number from the one already on the row: **4.6 s** is how long the clip plays
for, and **52.2 s** is how long it took to make. The Hub had been recording both all along; the app
was showing only the first, which is why the app appeared never to mention generation time at all.

### Technical notes

- **The number existed and was not read.** The Hub writes `runtime_s` on an item when it finishes
  and `run_started` when it starts, and `terminal_result` already carried the runtime - but
  `HubJobItem` parsed neither, and the rows showed only `audio_duration_s`, the length of the media
  produced. The owner read "4.6 s · 219 KB" as the app refusing to state a generation time, which is
  exactly what it was doing.
- `HubJobItem` now decodes `runtime_s` and `elapsed_s`, `NativeJob` carries `generationSeconds` /
  `runningSeconds`, and one `generationSuffix` renders as "· took 52.2 s" / "· running 12 s" (in
  minutes past a minute, and nothing at all when the Hub has not timed it). It is appended to the
  state line of all three job row styles, so the generator pages and the Jobs tab show the same
  thing by construction rather than by two edits.
- The Hub's item payload gained `run_started` and `elapsed_s`, because `runtime_s` is only written on
  completion: a generation that was still running had no duration in the payload at all.
- Evidence: `tests/test_generation_time.py` (4 checks: the payload carries both numbers, the app
  parses both, all three rows render it, and the clip length is not folded into the generation
  time), plus `tests/native/api_main.swift` for the formatting ("52.2 s", "2 min 5 s", nothing when
  untimed, nothing for a zero). Verified against the owner's own history by rendering the Audio page
  and the Jobs tab from the running Hub: five real clones at 62.0 / 79.7 / 43.0 / 52.2 / 45.4 s, a
  failed job at 2.3 s, and an image at 1 min 28 s.

## 0.5.4 — 2026-09-19 · the Generation column was clipping its own controls

### The Audio page's controls can be read and clicked now

The **Generation** column on the right of Audio was narrower than the controls inside it, so macOS
cut them off at the column's edge: the tabs read "Design a Voi…", the buttons came out as "Manage
Voice", "Transcribe Cl…", part of every explanation was missing, and the Speed row ran into itself.
The Script box was the other half of the problem — it grew as wide as the window would let it,
which left the controls even less room.

Every workspace now has the same shape as the Image generator: **what you set up on the left, what
you made on the right**, both starting at the top. Audio puts the script and all the voice settings
in that left column with the recent audio on the right; Subtitle puts the file, format, **Transcribe**
and its settings there with the transcript on the right; Image already worked this way. The three
voice tabs say **Clone**, **Ready-Made** and **Design** so they fit, labels sit above their controls
instead of wrapping, and the Models page no longer pushes its note to the foot of the window.

Nothing about generating audio, images or transcripts changed — only where things sit.

### Technical notes

- **The layout, not the logic.** `audioWorkspace` put a `TextEditor` with only a `minHeight` beside
  a fixed-width column. An unbounded editor takes every pixel the split will give, so the
  Generation column was squeezed to its floor of 280 pt while its contents - a segmented control
  with three long titles, a picker, wrapping captions - needed about 380, and SwiftUI clips content
  that overflows a frame. The result was a panel whose every control was cut off at the right edge
  and could not be clicked: the owner reported it three times, and each earlier fix changed copy
  rather than the layout.
- Fixed: the editor is bounded (`minHeight: 96, idealHeight: 150, maxHeight: 230`); the column asks
  for `minWidth: 330, idealWidth: 370, maxWidth: 460`; the tabs use short titles with the full name
  kept as an accessibility label and in the line below; captions that must wrap say so; the
  window's floor is now the arithmetic the columns need (`980x620`, was `720x520`, which could not
  hold a split page at all).
- **A way to look at the window.** `screencapture` is refused in this environment ("could not create
  image from display"), so three UI fixes were written blind. `native/render-page.sh` compiles the
  real views with its own entry point, hosts the page in an offscreen window and writes PNGs at the
  sizes that matter. It uses `NSHostingView` + `cacheDisplay` rather than `ImageRenderer`, which
  draws a yellow "unsupported" placeholder for AppKit-backed controls. Verified at 1920x1053,
  1100x700 and 980x620 before shipping.
- Second half, seen only after the first was rendered: with the editor bounded, the column became
  shorter than the split, and a split view **centres** a child shorter than itself - so the Script
  box floated a third of the way down the page with empty space above and below it. The column now
  fills the height and aligns `.topLeading`, and the recent-audio list takes the slack instead of
  stopping at a fixed maximum.
- **One house shape for the workspace pages, the Image page's.** The owner asked for the Image
  generator's left-aligned form on every page. Audio and Subtitle were restructured to match it -
  form on the left (script/voice; file/format/Transcribe/recognition), result on the right (recent
  audio; transcript plus recent subtitles) - and Image was brought onto the identical frame so the
  three cannot drift apart. Subtitle's primary action moved off the foot of a flexible column to
  sit with the file and format it acts on, and its "Result format" label moved above its control
  instead of wrapping in half. The Models page's library note was below a flexible split, so it
  appeared at the very bottom of the window; it now sits above the list. "This Mac is a Not
  reporting" became "This Mac is Not reporting".
- `tests/test_audio_layout.py` pins the arithmetic, the bounded editor, the shared column widths
  across all three workspace pages, the primary action/result placement, the short tab labels, the
  wrapping captions and the top alignment, so a revert fails the suite instead of the owner's
  screen. Both halves were found by rendering the page and looking at it - the second one only
  because the first fix was rendered before being handed over.

## 0.5.3 — 2026-09-19 · shared voices reach a node, measured rather than assumed

### A voice added once can now be shared with every Mac in a group

Adding a voice in the gateway can now put it on every Mac its controller owns, instead of copying
the clip from machine to machine. The controller does the distributing; the gateway only ever talks
to controllers, so it is one action per group rather than one per Mac.

A shared voice carries its **timings** as well as its words. That matters more than it sounds: a
long clip cannot be cloned without them, so a voice that arrived without them would look installed
on a hundred Macs and then refuse to clone on every one. The push is refused instead, at the moment
somebody can still fix it.

Two things were broken on the way to a node, and both were found by pushing a real voice to a real
joined Mac rather than by reading the code:

- A controller was not allowed to install a voice on a node at all — the request was refused, so
  nothing would ever have arrived.
- **Connections shows two tokens again, and they are not interchangeable.** *Hub token* is this
  Mac's own, for the gateway and your own tools. *Fleet token* is the one to give a Mac when it
  joins. Handing a node the Hub token would give whoever owns that Mac full authority over the
  controller; the Fleet token can send work and read results and nothing else. The join screen says
  which one it asks for.

### Technical notes

- **The Hub's shared-voice door exists.** `POST /api/hub/shared-voices` (create or refresh, then fan
  out), `GET /api/hub/shared-voices` (what is stored and how far each voice has reached),
  `GET /api/hub/shared-voices/{id}` and `/audio`, `POST /{id}/sync`, `DELETE /{id}` (tombstone, then
  remove on every node, awaited). `shared_voices` was complete with nothing calling it, and nothing
  started its reconciler either; both are wired now. `?wait=true` runs one fan-out pass before the
  answer, for a caller reporting per location.
- **The id may be the caller's.** A gateway that names a clip by its own content hash
  (`audio_sha256[:12]`) keeps that id, so the same bytes carry one id on all six controllers; the
  bytes decide identity, so a re-push refreshes that voice (200) rather than adding a copy, and an
  id already holding other audio is a 409. Without a `voice_id` the Hub mints one.
- **Timings are required with a transcript.** `PUT /api/voices/{id}/fleet-sync` takes
  `transcript_segments_json` and answers the count it stored, and the fan-out compares that count
  with what it sent: a node that drops them is `failed`, not `synchronized`. The 1.7B Qwen reference
  path passes the stored segments into `reference_audio.prepare`, which is what makes storing them
  anything other than decorative - it had the same gap for a voice prepared locally from a long clip.
- **Two blockers found by pushing a real voice to a real joined node:**
  - `PUT /studio/voice/api/voices/{id}/fleet-sync` was not on the fleet allowlist, so every install
    on a node answered 403 and nothing could ever reach a second Mac. Added narrowly, beside the
    voice `PATCH` it belongs with.
  - The Connections screen offered the **Hub** token for joining while `fleet_nodes` presents the
    **fleet** token to nodes: a Mac that joined correctly would have answered 401 to the controller
    it had just joined. I first "fixed" this by making a controller's fleet credential its Hub token;
    two tests in the suite caught it and it was the wrong answer - it hands full authority over a
    controller to every node it owns. Reverted: the screen offers the Fleet token, the join text and
    the kit say so, and the live fleet tests adopt the fleet token for the same reason.
- Tests: `tests/test_shared_voices.py` (17, unit) and `FleetSharedVoiceTests` (6, two real Hubs) -
  the timings travelling, a re-push not forking, a transcript without timings refused, removal
  tombstoned across controller and node, a node that drops the timings reported failed, and an
  unknown credential refused. 412 tests, 5 skips.

## 0.5.2 — 2026-09-19 · a restart no longer breaks the group

### A group of Macs now survives a restart

Two things made a fleet of Macs fragile. The Hub picked a **new port every time it started**, so
the address you wrote down for a controller stopped meaning anything the moment it restarted; and
a Mac that had joined one never re-joined on its own, so after a reboot somebody had to retype the
controller's address on every node.

- The Hub answers on the **same port every time** (47873), so an address stays true. If something
  else on that Mac is already using that port, the app says so before starting, instead of failing
  three times and reporting service failures.
- A Mac that has joined a controller **re-joins by itself** whenever its services start, which also
  tells the controller its own address again.
- **Connections** shows the port and the exact address to give a node, so setting up a group is:
  turn on a network address here, copy the token, paste the address and token there.

Both Macs need a network address turned on. A node has to be reachable for a controller to send it
work, and the Join form says so plainly if it is not.

### Leaving a group, and selling a Mac

A Mac that joined a controller now has **Leave this group** under **Connections**. It stops using
the group's token — this Mac mints a fresh one on the spot — and goes back to running on its own.
Use it before you retire, move or sell a Mac: the group's token is a file on that Mac, and without
this it travels with the machine.

Replacing a controller is the same idea from the other side: set the replacement to **Controller**
and it mints its own token, then each node joins it once with the new address and token. The old
machine's token stops working everywhere the moment that happens.

### Technical notes

- **Pin the Hub's port.** `packaging/hub_worker.py` takes `--port` and the app never passed one, so
  the Hub bound an ephemeral port and got a different one on every start. That made both directions
  of a join perishable: a node's stored controller address died when the controller restarted, and
  the controller's stored node address died when the node did, with nothing to repair either.
  `ShellConfiguration.port` (default `47873`, which is `peers.DEFAULT_HUB_PORT` - the number the
  fleet code already assumed when it built a peer's studio URL) is now saved and passed as
  `--port`, so an address written on one Mac still names that Mac tomorrow. Pinning it also makes
  the `DEFAULT_HUB_PORT` assumption true rather than about half true.
- **Fail before starting when the port is held.** `ShellConfiguration.requireFreePort` binds the
  wildcard address before anything is spawned. Without it a held port costs three failed starts and
  ends in "Automatic recovery stopped after repeated service failures" - over a number the operator
  never chose.
- **Re-join on start.** A successful join is remembered in the app preferences, and
  `NativeWorkspaceStore.rejoinControllerIfNeeded()` calls the join route again once the Hub is
  ready. That route is already idempotent ("record one node, or refresh what a rejoining node
  reports about itself"), which is what makes an automatic call safe. It runs only for a Mac whose
  credential origin is `adopted`: a controller's own credential is minted and its place is not
  inside another controller. Promotion to controller forgets the controller it used to follow.
  The re-join authenticates with this Mac's own fleet token, which *is* the controller's after
  adoption, so no second copy of a credential is stored anywhere.
- **Connections shows the port and the address to hand a node**, and says when no network address
  is on yet, which is the state that makes a join impossible rather than merely fail.
- **Leave this group.** `POST /api/hub/fleet/leave` mints a fresh local credential, records the
  origin as `minted`, returns the role to `standalone`, and stays outside the fleet credential's
  allowlist so a controller cannot un-join a node. The app asks the controller to drop the Mac from
  its roster first, while the borrowed token still works, then clears the remembered controller.
  This is the action for a Mac being retired, moved or sold - the group's token is a file on it.
- **Fix: the credential origin was never persisted.** `save_settings` writes an explicit key list
  and `fleet_credential_origin` was not on it, so `fleet_adopt`'s write was dropped and the
  in-memory cache was seeded without the key: every reader was told `minted`. Two consequences, both
  silent - the app's automatic re-join (which fires only for an adopted credential) could never run,
  and promotion containment rested solely on the operator having set the role to Node by hand. The
  live two-Hub test for leaving is what exposed it. `tests/test_fleet_credentials_origin.py` now
  asserts that every key in `defaults()` survives a settings write, so the next key added cannot be
  dropped the same way, and that adoption alone forces a rotation on promotion.
- Tests: the native fixture now serves `fleet_credential_origin` and asserts that a join is
  remembered, that an adopted Mac re-joins on start, that a minted one does not, and that becoming
  a controller forgets the old controller; `tests/native/main.swift` pins the pinned port, the
  `--port` override, legacy preferences, the port bounds and the held-port refusal;
  `tests/test_fleet_nodes.py` pins that a re-join with a new address updates the stored address and
  keeps the original `joined_at`.

## 0.5.1 — 2026-09-19 · a protected model library stopped the app from starting

### The app starts again, and the models have moved out of Downloads

Starting Studio Hub could wait forever on "Starting local services…", and all the app said was that
the services did not become ready. The reason was where the models were: the setup step put them in
**Downloads**, and macOS treats that folder as private. The first service to read its models was
stopped by macOS without being told why, so it waited inside that request instead of failing - no
error, no timeout, and no clue in the log.

The models now live in **~/StudioHubModels**, and the app starts in about two seconds. If you
already copied models into Downloads, the copy step moves them across the next time it runs, and a
library left in Downloads is still offered as a last resort. This replaces the note in 0.5.0 that
described a library in Downloads as the right place for it. If the app is pointed at a folder
macOS protects - Downloads, Desktop or Documents - it now says so before starting, instead of
waiting.

Nothing else changed.

### Technical notes

- Move the model library out of `~/Downloads`. A 34 GB library at `~/Downloads/model-library` made
  every launch hang: the app started all three workers, the Voice worker imported its generation
  store, printed `[gen] loaded 4 jobs from history`, and then blocked forever inside `open()`.
  `subprocess.poll()` stayed `None` for the whole 120 s wait, so the Hub never wrote
  `hub-ready.json` and the app reported only "the local services did not become ready within two
  minutes". No process logged anything: `sample` showed the worker asleep in the kernel in
  `_io_FileIO___init__` -> `open`, and `tccd` logged an access request against
  `kTCCServiceSystemPolicyAllFiles` attributed to this app. macOS privacy protection covers
  `~/Downloads`, and there a refusal arrives as a wait rather than an error. Moving the same
  library to `~/StudioHubModels` brought the Hub up with all five workers in about two seconds, and
  an empty folder outside Downloads did too - which ruled out the library's own contents.
- `ShellConfiguration.preparedLibrary` offers `~/StudioHubModels` first and still recognises
  `~/Downloads/model-library` for a Mac the earlier kit filled in. `protectedFolderWarning` names
  the protected folder, so the setup card and Settings can say why before a start is attempted
  rather than after two minutes of waiting.
- `qualification/copy_models_for_this_mac.py` writes to `~/StudioHubModels` and renames a legacy
  `~/Downloads/model-library` tree instead of copying 34 GB again; when both exist it names the
  leftover rather than deleting it.
- The kit's documents, the one-click steps and the release-kit manifest text follow the new path.
- `tests/test_peer_path_contract.py` looked for `Studio Hub 0.4.0.app` alone, so its live router
  probe skipped silently on the version-free bundle name. It now finds `Studio Hub.app` and the new
  library location and actually runs: 375 tests, 5 skips.

## 0.5.0 — 2026-09-17 · the app actually runs its own features

### The Transcribe button works, and you can pick a Whisper model again

Every time you pressed **Transcribe Clip**, the app was asking the Hub for a Studio that does not
exist, so the Hub refused it. The same mistake meant the app never learned which Whisper models
were installed, which is why there was nothing to choose on the Audio page.

Both are fixed. You can now press **Transcribe Clip** and pick **Whisper Turbo**, **Whisper Small**
or **Whisper Base**.

### Qwen3 TTS can actually clone your voice now

Four things were wrong at once, each one hiding the next. Pressing **Prepare Voice** and then
generating produced nothing, with a message about a missing reference or a "worker reference
invalid" error three layers away from the real cause. Fixed:

- The app was sending the reference clip under a name the Hub does not read, so the clip was
  quietly thrown away and the voice job ran with no voice attached.
- The smaller (4-bit) Qwen Base models had no approved reference-audio contract, so they refused
  every clone before it started. On an 8 GB Mac these are the only Qwen Base models that fit.
- When the voice engine refused a reference, the Hub ignored the refusal and ran the job anyway,
  which is why the error you saw never mentioned the real problem. It now shows the engine's own
  reason.
- A transcription of the reference clip is required for Qwen Base. **Transcribe Clip** fills it in
  for you, and now that it works, this step works too.

### Preparing a voice no longer needs timestamps typed by hand

A Qwen Base reference clip longer than 15 seconds needs a *timestamped* transcript so the engine can
choose the best 15 seconds of it. The app transcribed the clip and then threw the timings away, so
a long reference could not be prepared at all.

**Transcribe Clip** now keeps the timings from the same transcription and sends them along with the
reference. You do not have to do anything differently: record or choose a clip, press
**Transcribe Clip**, check the words, then press **Prepare Voice**.

### The app now uses the models you copied

The app was reading a different, older copy of the models, so the three new voice models read as
"not installed" and the Audio page's model list came up empty. It now uses
`Downloads/model-library`, which is the folder **2 - Copy the models** fills in.

If you ever see a model the app calls "not installed" when you know it is there, this is the first
thing to check: open **Settings** and look at **Model library**. It should say
`Downloads/model-library`.

### Studio Hub has an icon

It was shipping without one, so the Dock, the app switcher and the About panel all showed the blank
page macOS uses when a bundle has no artwork. It now has its own: a broadcast source above a
waveform, which is what the app is — a Hub with Studios around it, carrying sound and picture.

### Your finished audio and images open again after a restart

A finished generation could stop opening. Pressing **Play**, **Save** or **Open Transcript** on
something you made earlier gave:

> Local engine http 502: worker artifact reference is invalid

The cause was a stored address. Each time the app starts, its studios get new port numbers, but the
record of a finished job kept the *old* port — so after a restart the app was told to collect the
file from a door that no longer existed. The file itself was always safe on disk.

The app now records where the file is *inside* its studio rather than the full address, and works out
the address fresh each time. Everything you made before this update opens too — no need to re-make
anything.

### You can now change how fast the audio speaks

The engine has always been able to speed audio up or slow it down **without changing the voice** —
it has been doing the long-script splitting and joining all along too, automatically. What was
missing was any way to set the pace from the window.

There is now a **Speed** slider on the Audio page, next to Language: 0.5× to 2×, with **Normal** to
put it back. It applies to every voice — your saved voices, the ready-made ones, and designed ones.

Measured on this Mac:

| Setting | Length of the same sentence |
|---|---|
| 0.5× (slowest) | 12.3 s |
| 1× (normal) | 4.9 s |
| 2× (fastest) | 2.5 s |

### Long scripts already split and rejoin by themselves

Nothing to set. A script longer than the model can speak in one go is split at sentence boundaries,
each part is generated, and the parts are rejoined with a short pause — with the engine's own
cleanup of any silence the model adds at the end of a part. You see it as `section 3/7` progress.

### Whisper Base and Small work again

**Whisper Base** and **Whisper Small** refused to transcribe, saying the model's config was missing
even though it was sitting in the model folder. **Whisper Turbo** was unaffected, which is what
pointed at the cause.

The two broken ones are stored in a form the engine has to convert, and that conversion is cached
in a folder built from links into your model library. When the library moved, every link in that
cached folder broke — and the app kept trusting the folder because it looked finished. It now
checks that the cached copy actually points at real files, and rebuilds it when it does not.

All three Whisper models transcribe correctly now.

### The Subtitle page no longer offers to play a transcript

**Recent subtitles** was showing **Play** and **Save** for transcripts, which are text — and
**Open Transcript**, which did nothing except switch pages and leave the page empty. Both fixed:
the row now offers **Open Transcript** and it actually loads the transcript, so you can read it,
switch between Text / SRT / VTT and export it.

Finished transcripts also stopped carrying an old warning. Every one of them said *"model is not in
the packaged catalog"* — a note from a retry, left on a job that had since completed successfully.

### The Audio page is now three tabs

Choosing a voice is really three different jobs, and they used to share one form whose controls
changed under you depending on the model — with nothing saying why. Now each is a tab:

- **Clone a Voice** — use a voice you saved, or a clip from disk. Yours are all here.
- **Ready-Made Voices** — voices built into the model. Pick one from a list and type your script.
  Nothing to prepare. This needs the **Qwen3-TTS CustomVoice** model.
- **Design a Voice** — describe what you want in words, for example *"a deep, slow and warm
  narrator"*, and the model makes it. This needs the **Qwen3-TTS VoiceDesign** model.

Each tab lists only the models that do that job, and tells you which model to install if you do not
have one yet. The three model tiers that make this possible are now in the kit, so **2 - Copy the
models** can bring them over.

### Every voice plays now, and any of them can be picked in Audio

Two things were wrong, and both are fixed.

**Seven of your ten voices would not play.** They are MP3 files, and the player could not read an
MP3 handed to it in memory — it reported an "OSStatus error -4". Playing them from a file works, so
that is what the app does now. All ten play.

**The Audio page had no way to pick a saved voice.** It only ever offered a clip file, even though
the app keeps a voice library and the engine has always accepted a saved voice by name. So a voice
sitting in **Voices**, ready to clone, could not be used where you generate. The Audio page now
starts with a **Voice** list: choose one of your saved voices and type your script. There is nothing
to prepare — the clip, its words and its timing are already with the voice.

Choosing **Choose a clip file…** instead gives you the old behaviour, with **Transcribe Clip** and
**Prepare Voice** as before.

### Your ten saved voices come with the kit

**2 - Copy the models** now brings your voices too — Christian, Vincent, Leo, Samuel, Todd, Archer,
Aiden, Mark, Leona and Chris. Open the app, go to **Voices**, press **Refresh**, and they are all
there, ready to clone with.

A voice already on a Mac is never overwritten, so anything you have changed there is kept.

Clips longer than about 15 seconds already carry the timing the app needs, so they work straight
away instead of asking you to transcribe them first.

### Copying models keeps them up to date

**2 - Copy the models** is now safe and useful to run again at any time:

- models you do not have yet are copied;
- models that changed on the SSD are replaced in place;
- models that are already correct are left alone, so a second run takes about a second instead of
  several minutes;
- an older version of a model that was just updated is removed, so your Mac does not slowly fill
  up with copies you no longer use;
- the app's record is refreshed as it goes, so an updated model reads as installed instead of
  asking you to verify 29 GB by hand.

### The two setup files now work wherever they sit

**1 - Install the app** and **2 - Copy the models** used to fail when run from the top of the
drive, saying they could not find Studio Hub. Both now work from the top of the drive or from the
Technical folder.

### Opening the app for the first time is one click

The app used to ask you to find your model folder in a file panel. Now, when the models
have already been copied to your Mac, it says so and offers a single button:

> **Use the Models Already on This Mac**

Press it and everything starts. Before this, the only prominent button on a new Mac was
"Use Default Folder" — an empty folder inside the app that would have downloaded all
29 GB again.

### This screen

Open **What's New** from the sidebar, from the app menu, or from the menu bar icon. It opens by
itself the first time you run a new version, and then stays out of the way.

### Technical notes

Owner-facing notes for this release are in `release-notes.md`, which is what the app's **What's
New** screen shows.

- Fix the peer-path modality. The app asked the Hub for `/studio/subtitle/...`; the Hub's fleet
  surface routes `/studio/<modality>/...` and knows only `image`, `voice` and `transcription`, so
  it answered `404 {"detail":"unknown studio"}`. Both the synchronous Transcribe route and the
  transcription availability read were affected; the second meant Whisper models never reached the
  Audio page, so no transcription model could be selected. The Swift fixture had been written
  against the app's own spelling, which is why every gate passed. It now refuses an unrouted studio
  exactly as the Hub does, and `tests/test_peer_path_contract.py` compares the app's paths with the
  Hub's routing table and with a running Hub.
- Fix the Hub job envelope encoding. `sendHubJSON` used a bare `JSONEncoder()` while `sendJSON`
  converted keys to snake_case, so `voiceReferenceAssetId` reached the Hub while it reads
  `voice_reference_asset_id`. The Hub ignores unknown keys, so the reference was discarded and
  every Qwen3-TTS Base clone failed with a message about a missing reference. `clientRequestId` was
  lost the same way, defeating the Hub's replay protection.
- Add the missing reference-audio contract for the 4-bit Qwen3-TTS Base tiers, derived from the
  audited 8-bit sibling of the same size and upstream model by
  `packaging/derive_qwen_base_audits.py`. A quantization changes weight precision, not the audio
  front end; the generated records carry a `derivation` block and claim no independent audit. On an
  8 GB node the 4-bit tier is the only Qwen Base tier that fits.
- Stop discarding a refused voice reference. The Hub now surfaces the worker's own reason, code
  included, instead of running a job with no reference attached.
- Make the model copy incremental and re-runnable: copy what is missing, replace what changed,
  leave identical models untouched, prune the superseded revision, and rewrite the app's receipt so
  an updated model reads as installed.
- Make both kit launchers location-independent; the copy in the kit root had resolved its helper
  one directory above the kit and refused to run.
- Return the engine's timed segments from the synchronous transcribe route instead of `null`, and
  keep them in the app so **Prepare Voice** can send them with the reference. A Qwen3-TTS Base
  reference longer than the model's 15-second maximum needs timestamped segments to select a
  bounded, speech-aligned window; without them the engine refused with
  `REFERENCE_TRANSCRIPT_ALIGNMENT_REQUIRED`. Timings are sent only while they still match the
  transcript, so a hand-edited transcript is never windowed against stale timings.
- Carry the owner's voices in the kit. `2 - Copy the models` now adds the voices the kit holds
  alongside the models, and never overwrites a voice already on a Mac, so a locally corrected
  transcript or replaced clip survives. Ten voices, all with transcripts, arrive ready to clone.
- Ship reference timings with the kit's long voice clips. Seven of the ten are 15.4–22.2 s, and a
  Qwen3-TTS Base reference over the model's 15-second maximum needs timestamped segments to be
  windowed; `qualification/time_kit_voices.py` computes them once at kit-build time with the app's
  own Whisper and the app reads them, so no Mac has to transcribe a voice before using it.
- Offer the library the one-click setup filled in. On a fresh Mac the setup screen's only
  prominent button was "Use Default Folder", which points at an empty folder inside the app's data
  directory and invites a second, 29 GB download of models already sitting in Downloads. The app
  now recognises `~/Downloads/model-library` by its install record and offers
  **Use the Models Already on This Mac** as one click, and as the first choice.
- Play a saved voice from a file, not from memory. `AVAudioPlayer(data:)` cannot identify an MP3
  handed to it as bytes and fails with `OSStatus error -4` - measured on this Mac against the same
  files `afinfo` reads without complaint. Seven of the ten saved voices are MP3, which is exactly
  the set that would not play. The clip is now written under the app's own directory with the
  extension the worker reports and removed when playback stops. A finished job's audio is played
  the same way, with its container read from its own bytes rather than assumed.
- Offer the saved voices on the Audio page. The Voice library and the worker's `voice_library_id`
  contract both existed; the page only ever offered a clip file, so a voice the operator could see
  in **Voices**, ready to clone, could not be chosen where they generate. The page now has a Voice
  list, and picking a saved voice skips Prepare Voice entirely because the clip, transcript and
  timings are already stored with it.
- Lift `voice_library_id` from the top level of a job envelope in the Hub, as
  `voice_reference_asset_id` already was. Without it the saved voice was dropped on the way to the
  worker, which then refused with a message about a missing reference the operator had never been
  asked for - the same silent-drop shape as the camelCase envelope bug.
- Organise Audio into three voice tabs: cloning, ready-made voices, and voice design. The page was
  one form whose controls changed with the selected model and never said why, so which models did
  which job was only discoverable by trying. Models now carry a `VoiceMode`, each tab lists the
  models for its job and its own controls, and switching tabs keeps the selected model consistent
  with the tab. A model with no name hint and no clone capability is the preset form, since that is
  the only Qwen mode needing nothing but a speaker name.
- Capture and ship the preset-speaker and voice-design Qwen3-TTS tiers
  (`qwen3-tts-12hz-0-6b-customvoice-8bit`, `qwen3-tts-12hz-1-7b-customvoice-8bit`,
  `qwen3-tts-12hz-1-7b-voicedesign-8bit`). The kit shipped only the cloning tiers, so the Audio page
  could offer cloning and nothing else no matter how it was arranged.
- Point the packaged app at the library the setup step fills in. A development checkout had left a
  second `model-library` beside the source, and the installed app was bound to that older copy, so
  the three newly captured voice tiers read as `not_installed` and the Audio page's model list was
  empty. Measured on the running app: the Hub was serving `--models-dir
  .../studiohub-standalone/model-library` with 12 verified packages instead of the 15 in
  `~/Downloads`. The stale Hub was stopped, the duplicate folder moved to the Trash, and the app's
  own `app-preferences.json` points at `~/Downloads/model-library`; the running app now reports
  `15 of 15 ready`.
- Draw subtitle lists with the subtitle row everywhere. The Subtitle page's "Recent subtitles" panel
  was the last place using the audio row, so it offered Play and Save for a transcript while the
  Jobs page offered "Open Transcript" for the same job.
- Make "Open Transcript" load the transcript. It switched pages and left them empty; it now reads
  the finished batch's own SRT artifact and derives the plain text and WebVTT from those cues, so
  the format picker and Export work on a transcript from an earlier sitting.
- Report `governor_note` only while a batch is still waiting. The note explains why a queued job
  could not be dispatched and is cleared on a successful dispatch, but one written on an attempt
  that was later retried into success stayed on the record: all eleven transcripts in the owner's
  history carried "model is not in the packaged catalog" next to a finished result.
- Rebuild a cached model view when the library it was built from moves. Views are made of absolute
  symlinks, so moving the library leaves every link dangling while a marker file still calls the
  view finished - and it is reused forever. Seen in two states on this Mac: a view whose links all
  dangle, and a view directory with no entries. Both are now detected and rebuilt, in
  `cache._merged_snapshot_view()` and in the Subtitle engine's own view builder. This is why
  `whisper-base` and `whisper-small` failed with "Config not found" while `whisper-turbo` - which
  needs no view - kept working. All three verified transcribing after the fix.
- Expose the speaking pace in the app. The engine has applied pitch-preserving tempo to the
  finished audio all along, and the audited contract has carried `speed` (0.5-2.0) since the start of
  the project, but nothing ever sent it: every clip was generated at 1.0. `HubAudioParams.speed` is
  now sent on all four voice paths and a slider sets it. Measured through the real worker, one
  sentence: 12.29 s at 0.5x, 4.88 s at 1.0x, 2.51 s at 2.0x - monotonic and correct in direction,
  and not exactly linear because the model's own generation length varies between runs.
- Keep a finished artifact readable across a Hub restart. Worker ports are assigned at start, but
  the job item stored the absolute worker URL the Hub built at dispatch time - including the port.
  `job_artifact` requires a stored URL to share the worker's *current* origin, which a stored
  absolute URL can never do again, so it answered 502 "worker artifact reference is invalid" for
  files that were on disk the whole time. Measured 2026-09-18: a Voice job completed at
  `127.0.0.1:65461`, the worker came back on `127.0.0.1:58107`, and every Voice and Image artifact
  in the history became unopenable. The job item now stores the worker-relative path, the asset
  record still gets the absolute URL it fetches from, and `ledger.load_batch` normalises an
  absolute URL to its path on read so batches written before this change keep working without a
  migration. Both old artifacts return 200 after the fix.
- Give the app an icon. The bundle had none - no `.icns` and no `CFBundleIconFile` - so macOS drew
  the blank page placeholder everywhere an icon appears. `packaging/make_app_icon.py` draws the mark
  with CoreGraphics and assembles the `.icns` from one 1024px master, so every size is a true
  downscale rather than separately tuned bitmaps. Staging copies it, the plist names it, and staging
  now refuses to build when it is missing.
- Add **What's New** to the app, fed by the bundled `release-notes.md`, shown once after an update.

## 0.4.0 — 2026-09-15 · bundled Hub development proof

- The app runs everything locally: a Hub with Image, Voice and Subtitle studios inside the app.
- Image, Subtitle, Qwen voice cloning and OmniVoice all produce real output.
- Per-page history, plus one Jobs page with a tab per studio.
- Models can be downloaded, imported from a folder, verified, and kept outside the app.
- Settings shows what each studio is holding in memory and lets you release it.

### Technical notes

- Route the native workspace through the bundled authenticated Hub and its private Voice child, reusing the existing Hub scheduler and durable job identities.
- Add external model-package management, editable device output-policy defaults, packaged release identity and independent Subtitle registry support.
- Stop owned workers on app/Hub exit and surface private-worker failure through the Hub owner. Keep model files and device state outside the app.
- Preserve the historical 0.3.0 preview artifact. This development build is not a complete narrowed release: FLUX, Qwen Base controls, independent Subtitle process, cleanup enforcement and fleet integration remain later phases.
- Model-file verification and local process tests do not qualify model inference, original M1/Sonoma, login startup, signing or distribution.

### Included ST-01 foundation

- Pin ten scoped model packages and companion assets to immutable revisions and file hashes.
- Add verified HTTPS download, resumable staging, offline folder import and registration in place; preserve a working revision on failed replacement.
- Record separate Image/Voice runtime inputs, native binary floors and redistribution notices, with Image relocation and physical-device qualification still pending.
- Preserve GenStudio-to-location-controller-to-nodes routing and document the next Hub integration handoff.
- Keep the existing 0.3.0 preview artifact unchanged; the foundation checkpoint itself did not replace that app.

## 0.3.0 — 2026-09-11

### Technical notes

- Redesigned the application as a native SwiftUI/AppKit shell with six visible functions: Audio, Transcription, Image, Music, Video and Chat. The embedded browser workspace is removed.
- Separated Audio and Transcription state and added shared Overview, Models, Jobs and Settings pages. Connections to an API, Studio Hub or GenStudio are optional; local asset creation is primary.
- Recorded native Audio job `c56f0f41d938` as completed and saved. Whisper transcription verification is underway. Image, Chat, Music, Video and optional controller integration remain pending.
- Removed the word-timestamp control because the word-level result viewer is not implemented. Native Transcription supports Text, SRT and VTT results.
- The build selects the macOS 26.5 SDK because installed SDK 27 is missing the SwiftUI macros plugin; the deployment target remains macOS 14. Sonoma compatibility has only been tested on macOS 26, so physical Sonoma compatibility remains unqualified.
- No signing, notarization or login-registration tests are claimed. The planned local artifact is `dist/Studio Hub.app`; no remote artifact is planned.

## 0.2.0 — native Audio and Transcription preview

### Technical notes

- Added a SwiftUI/AppKit window with Audio, Transcription, Image, Music, Video and Chat navigation; Audio and Transcription connect to the bundled Voice worker.
- Added model-folder setup, preserved editor state, native upload/save dialogs, loopback-only embedded navigation and menu-bar reopening.
- Added optional login registration, explicit quit/stop handling, scoped worker ownership and startup readiness.
- Image, Music, Video, Chat and bundled Hub/fleet integration remain implementation work. Sonoma, login startup, clean-machine distribution and other models remain unqualified.

## 0.1.0 — local Voice packaging proof

### Technical notes

- Added an isolated, pinned Voice source adoption and a bundled Python runtime build.
- Added external node state and model storage, bundle-relative startup, and immutable release metadata.
- Disabled inherited Pinokio maintenance and withheld upstream model qualification for the new runtime.
- Added local relocation and API smoke checks. Sonoma, full fleet integration, signing, and the native app shell remain qualification/implementation work.
