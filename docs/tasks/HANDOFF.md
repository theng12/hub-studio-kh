# Studio Hub — session handoff

**Read this first in a new session.** It is kept current with every release; see the rule in
`AGENTS.md`. If it disagrees with the code, the code wins — and then fix this file.

Last updated: 0.6.56.

**0.6.56 (hotfix) — 8 GB Macs could not generate images.** `7187d8d` (0.6.55) made a
model's RAM *qualification tier* the first `blockReason`, so the Image worker's advisory
`memory_warning` set `runtimeReady=false` and `usableModel` refused every submit. FLUX.2
Klein was unusable on the whole 8 GB tier (Chat too); 16/24/32 GB were never affected. Fixed
in `native/StudioAPI.swift` (`blockReason` no longer returns the warning; the worker's
`memoryEligible == false` still blocks). Image label also restored to
`FLUX.2 Klein 4B MLX 4-bit`. Native boundary/API/lifecycle suites and 14/14 Image tests
pass. If a future change wants the tier to gate dispatch, it must do it in the worker or the
Hub's admission — never in the app's `blockReason`. This hotfix was cut from `9756528` in a
clean worktree, deliberately excluding uncommitted work in the main checkout.

## Where things stand

- Release **0.6.55**. Every release is pushed to `main` and to
  `implementation/voice-runtime`; `main` is the release branch. **0.6.55 is cut locally but not
  yet pushed**: staged, installed to `/Applications`, DMG built, and all four drive kits
  refreshed. The pushing owner still has to run the two pushes in step 8.
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
- **Khmer quality is ear-only and not yet judged by the owner.** Generated clips are in
  `.build/khmer-adoption/audio/`. Whisper cannot score Khmer (it labels even the purpose-built
  MMS model Vietnamese), so a passing test is not a quality claim.
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
