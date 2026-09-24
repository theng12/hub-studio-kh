# Studio Hub — session handoff

**Read this first in a new session.** It is kept current with every release; see the rule in
`AGENTS.md`. If it disagrees with the code, the code wins — and then fix this file.

Last updated: 0.6.57 (fleet pool visibility, 2026-09-24).

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

- **0.6.55 is local only.** Staged at `.build/StudioHub-0655`, installed, DMG at
  `dist/StudioHub-0.6.55-3ab8984.dmg`, four kits refreshed, `VERSION`/`CHANGELOG`/`release-notes`
  written, and the implementation committed locally at `3ab8984`. **Not pushed.** The owner runs
  the two pushes (step 8) and updates `main`. The Khmer adoption is written up in
  `research/khmer-tts-adoption-2026-09-24.md` and tracked by `docs/tasks/ST-12.md`.
- **ST-13 TTS menu pruning — source, tests and catalog are green; the drives need one final
  re-stage.** The owner cut the Voice menu after a full listening review
  (`research/tts-archive-walkthrough-2026-09-24.md`, `research/tts-offer-review-2026-09-24.md`).
  `catalog.py` carries the reduced menu; the 18 cut manifests are preserved in
  `_trash/model-manifests-pruned-2026-09-24/`; all four kit libraries and the Mac library are
  pruned; the capture tool's cut rows are removed. **The reduced-menu tests are green** — the
  eight voice modules run **216 passed / 2 skipped**, and `test_qwen_base_standalone`,
  `test_section_policy` and `test_model_manifest_catalog` all pass. The earlier "~58 failures"
  claim in this file was stale. **The drives' installed app still predates the reduced Voice
  catalog**, so a re-stage is owed: `verify-kit.sh` passes on the pruned libraries, but the app
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
  **Kit stocking is done** — all four `KH-STUDIO-*` kits carry the chat-capable app (`4e87afd`)
  and the 5.98 GB model, and each verifies 20 installed / 20 advertised, 181 files, 64.5 GB,
  `RESULT: PASS`. **Still owed:** 8/16/24 GB throughput is unmeasured, the packaged GUI
  interaction is unproven, and **ST-14.8 independent review has not happened**. gateway-kh remains read-only here and does not
  serve chat on its `studio_hub` kind (`research/chat-studio-gateway-bridge.md`). Streaming
  through the Hub is buffered by `studio_proxy`, so the app uses non-streaming.
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
