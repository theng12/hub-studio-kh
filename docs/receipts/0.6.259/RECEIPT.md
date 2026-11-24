# Release receipt — hub 0.6.259

app: hub
version: 0.6.259
commit: bcc5fe6795123f1c85c6af3783197e97ba33ffb4 (built) — merged into `origin/main` at
  de94170ad95cada719459e70c390f92672a91df5 (`Merge release/0.6.259 into main`); the merge adds no change
  to the tree the DMG was built from. Branch `release/0.6.259`, worktree `hub-studio-kh/worktrees/0.6.259`.
  Commits, oldest first: `144ce6c2` (the import queue), `fd9bd609` (resume route, an import answers a run),
  `cfbbbe19` (a folder import joins the queue; queued rows counted), `a6fdf637` (native contract expects a
  queue run), `6898b5bd` (native fixtures carry the queued count), `7c208640` (render fixtures carry
  position/reason + a queued and a paused row), `89b737fc` (the Jobs page row draws waiting/paused),
  `bf789ff4` (the chip's words are one function; the view check reads it), `a2f6a537` (screenshots +
  README), `bcc5fe67` (**the artifact's commit** — a drive run waits for the mount point).
artifact: `$HOME/Developer/_orchestra/developer-media/hub-studio-kh/app/dist/Hub Studio KH-0.6.259.dmg`
sha256: 7b1542105dadadb14c333b335b2f8bee7a431e49a1af891cf3fff64e4661ee5c
bytes: 1483071488
scan: sha256=7b1542105dadadb14c333b335b2f8bee7a431e49a1af891cf3fff64e4661ee5c clean — `scan-dmg.sh`
  scan-dmg/2026-11-20, exit 0, 67,271 files, 452 s; OWNER HITS none (the `100.64.0.0/10` range literal is
  the accepted, filtered tailnet range); UNKNOWN none; vendor licence and credit addresses excused by path
gate config: `$HOME/Developer/_orchestra/developer-media/hub-studio-kh/lead/release-0.6.259-20261124/gate-0.6.259.sh`
gate exit: 0 — `GATE PASS hub bcc5fe6795123f1c85c6af3783197e97ba33ffb4 2026-11-24T18:51:22Z`
gate evidence: `lead/gate-20261125-004811-50786` (run 6). Runner `gate-run.sh f05b010c16e5`.
feed readback: hub 0.6.259 — fresh anonymous read of `https://theng12.github.io/kh-releases/release.json`
  (`published_at 2026-11-24T20:02:00.067Z`): `version 0.6.259`, `file Hub.Studio.KH-0.6.259.dmg`,
  `bytes 1483071488`, `sha256 7b1542105dadadb14c333b335b2f8bee7a431e49a1af891cf3fff64e4661ee5c`,
  `type media`, `tagline "Runs AI models on this Mac for your other apps."`,
  `holdURL http://127.0.0.1:47873/api/maintenance/hold`, `holdSince 0.6.238`,
  `url https://github.com/theng12/hub-studio-kh/releases/download/0.6.259/Hub.Studio.KH-0.6.259.dmg`.
  `feed-verify.sh hub --expect-version 0.6.259 --expect-sha 7b154210…6e5c --public-key …` →
  **PASS** (`PASS signature — Ed25519 verified`, 165,640 feed bytes, 64 signature bytes).
  Asset anonymous `curl -L` readback (no auth header, `evidence/anon-readback-0.6.259.log`):
  HTTP 200, 1,483,071,488 bytes, sha256 `7b1542105dadadb14c333b335b2f8bee7a431e49a1af891cf3fff64e4661ee5c` —
  equal to the gated artifact and scan receipt.
page state: same — the served row's url, file, length, sha256, tagline and hold pair match the published asset and gated bytes
dirty: false
idle footprint: **95 MB after 600 s, 28 MB lower than at start**, ceiling 500 MB — measured by the gate
  config's own `idle-footprint` step, which started the staged build's Hub worker (`hub_worker.py`, the
  service half) hidden, with a temp data dir and HOME, health 200, then the standard 600 s window. The
  runner's built-in `idle-footprint` line in the same run reads UNMEASURED because `IDLE_PID` is not set in
  the config; both facts are true. 0.6.258's equivalent figure was 64 MB / −58 MB, and 0.6.257's shell-only
  figure (service not started) was 103 → 75 MB — this number includes the service.
retired: 0.6.257 installer (1,483,247,616 bytes, sha256 `e52628d63de1a261c14723aa282884f73626fe1786365a8aff5a34d09c933b6c`) plus `worktrees/0.6.258` (`4dd1f590`) and `worktrees/0.6.259` (`bcc5fe67`) moved to `$HOME/Desktop/_trash/hub-studio-kh/` with manifests (nothing freed until the owner empties the trash)
not proven: (1) live self-update on this Mac (0.6.258 → 0.6.259) stopped at the visible-window guard:
  neither `Hub Studio KH.app` nor `Studio Launcher KH.app` is installed in `/Applications`,
  `com.khupdater.agent` is unloaded (`~/Library/KHUpdater/base/config.json` has no `hub` entry), and
  `Recipes.swift:1482` relaunches with `open -g -j` while `native/StudioHub.swift:1337,1457-1459` calls
  `openWindow()` (`window?.makeKeyAndOrderFront(nil)` + `NSApp.activate(ignoringOtherApps: true)`) whenever
  `!loginLaunch || !controller.hasLibrary` (`~/StudioHubModels` is absent here) — so a live relaunch would
  bring a visible window onto the owner's desktop; (2) whole-fleet / long-run states of the idle-footprint
  acceptance — only the idle-after-one-cycle state was measured.

## Gate runs — every attempt, kept as what it was

| # | evidence | outcome |
|---|---|---|
| 1 | `gate-20261124-172938-53750` | **FAILED `image-runtime`** — `.build/downloads/mlx-serve-bin-macos-arm64.tar.gz` was missing and nothing in the tree fetches it. Re-fetched from `packaging/image-runtime/mlx-serve-pin.json` `source_url`; sha256 `e53056e481364ff72188fafea8b3eb0aeb0b5b7cebcd6e6e7d26bf1ce4223873` **matches the pin**, 76,304,537 bytes |
| 2 | `gate-20261124-174527-8241` | **FAILED `suites`** — `image-runtime`, `seed-kit`, `native` all ok. 10 suites green; two were short module paths in this config (`tests.test_fleet_model_install`, `tests.test_lan_model_share` live under `tests/hub_regression/cases/`); `test_kit_drives` was a **real defect** (below) |
| 3 | `gate-20261124-212517-45512` | **INVALID `stage`** — runner's own line: machine not quiet (14,540 MB available, 5,778.31 of 6,144.00 MB swap). Also found a real fault: the YuE2 runtime in `.build` hashed `3878cd9e…` against the pin `e2e4d1df…` |
| 4–8 | `gate-20261125-001705-5309`, `-002025-6353`, `-002325-7958`, `-002612-8878`, `-003613-13936` | **refused before the lane** — `GATE REFUSED: swap is 92–94% full`. No lane taken, no step run, nothing measured |
| 9 | `gate-20261125-004613-43327` | **FAILED `preflight`** — `MISSING-INPUT leftover … .build/staged-259/Hub Studio KH.app` (run 3's staged app). Moved aside; nothing deleted |
| 10 | `gate-20261125-004811-50786` | **GATE PASS** at `bcc5fe67`; artifact recorded with dev/ino/size/mtime/ctime identity; scan clean |

**The defect the gate caught and this release fixes** (`bcc5fe67`): the queue paused any run whose
`request.drive` did not exist, and that path is the model-library folder *inside* the mount. A drive that
was attached but whose library folder was not yet created paused forever with "not mounted" instead of
copying. The pause now asks whether the **mount point** exists (`_drive_root`, the same `KH-STUDIO-` parent
walk the source name already used); a drive that is attached with no library is the child's failure to name.

**The finding worth keeping** (0.6.258's gate, not this one): `tests/hub_regression/cases/test_fleet_model_install`
and `…test_lan_model_share` are **pytest** modules. Under `unittest` they import, collect zero tests, and the
runner's `rc=5` fallback executed the file and exited 0 — so 0.6.258 counted both green **without running a
test**. This config runs them with `-m pytest -q` and counts a suite only when rc=0 **and** at least one test
passed: 19 and 21 passed here. Issue rows: `20261124-69` (the drifted row), M001's zero-collected-tests row,
M001's shortened-suite-path row.

## Tests run in the passing gate

`tests.test_model_download_queue` 8 · `test_model_downloads` 2 · `test_model_download_space` 4 ·
`test_download_progress_honesty` 4 · `test_model_install_plan` 5 · `test_model_packages` 81 ·
`test_hub_model_packages` 13 · `test_hub_model_presets_route` 21 · `test_hub_ops_visibility` 6 ·
`test_kit_drives` 14 · `test_developer_test_service` 20 · pytest: `test_fleet_model_install` 19,
`test_lan_model_share` 21 — **0 failed**. Native: boundary, API, secret-masking, view and lifecycle checks
all passed inside `native/build.sh`, including the new `downloadBadge` assertions (a waiting copy names its
place, a paused one says paused, a copying run shows its percentage). Suites peak 205 MB over 52 s.

## Screenshots (owner rule §5d)

Four images in `docs/screenshots/`, rendered **offscreen** from this build (`native/render-page.sh`,
`STUDIOHUB_RENDER_FIXTURES=1`, `STUDIOHUB_RENDER_JOBS_TABS=1`, `STUDIOHUB_RENDER_APPEARANCE=both`,
1920x1053) — no window, no screen capture, no system permission dialog, demo content only:

| file | read |
|---|---|
| `downloads-light.png` | Jobs → Downloads: one copy at 42%, one **waiting in line · position 1**, one **paused** with "ATLAS is not connected" and Resume |
| `downloads-dark.png` | same, dark |
| `overview-light.png` | overview with demo jobs and models |
| `overview-dark.png` | same, dark |

`check-app-screenshots.sh`: **PASS: 4 screenshots, each within size, named, referenced, and clean in text
and metadata** (OCR row included). The 0.6.258 Connections pair was retired to the Desktop trash with a
manifest; the checker allows four and each must come from the build being released.

## Changelog

`CHANGELOG.md` keeps the ten most recent releases; older sections live unedited in
`docs/changelog-archive.md` (`grep -c '^## '` → 10 and 263). Release notes: the two-sentence summary in
`evidence/release-notes-0.6.259.md` (72 words), not the changelog.

## App identity and the feed row

`app-meta.json` is in the repo at the tag (the item 0.6.258's receipt listed as `next`): `name` Hub Studio KH,
`bundle_id` `local.studiohub.standalone`, `bundle_name` `Hub Studio KH.app`, `type` `media`, the approved
tagline and description, `hold_url` `http://127.0.0.1:47873/api/maintenance/hold`, `hold_since` 0.6.238,
`kind` `app-dmg`. It is excluded from the packaged file list.

The row is **not signed here** (M001, 2026-11-24). Values handed to S001's generator pass:
- version `0.6.259`
- stored asset name `Hub.Studio.KH-0.6.259.dmg` (GitHub rewrites spaces to dots and keeps them)
- sha256 `7b1542105dadadb14c333b335b2f8bee7a431e49a1af891cf3fff64e4661ee5c`
- bytes 1,483,071,488
- `browser_download_url` `https://github.com/theng12/hub-studio-kh/releases/download/0.6.259/Hub.Studio.KH-0.6.259.dmg`
- tagline `Runs AI models on this Mac for your other apps.`; the approved description; `notes` the file above

## Merge into main

`release/0.6.259` pushed (`origin/release/0.6.259` = `bcc5fe67`), then merged into `origin/main`
(`bbf087bb` → **`de94170a`**, `--no-ff`), pushed, and proven:
`git merge-base --is-ancestor bcc5fe6795123f1c85c6af3783197e97ba33ffb4 origin/main` → yes. The merge is
identical in content to the release branch (`git diff release/0.6.259 origin/main` empty at merge time).

**Local checkout trap fixed:** the canonical checkout's `main` was the pre-rewrite line (`460586a4`, no
upstream). Renamed to **`pre-rewrite-main`** (kept, never deleted) and `main` re-created from `origin/main`,
tracking it. `pre-rewrite-main` is the receipt's record of that branch's continued existence.

## Closeout

retired: `Hub Studio KH-0.6.257.dmg` (1,483,247,616 bytes, sha256 `e52628d63de1a261c14723aa282884f73626fe1786365a8aff5a34d09c933b6c`) moved to `$HOME/Desktop/_trash/hub-studio-kh/2026-11-25 0.6.257 installer/` with `MANIFEST.md` (size and sha256 verified equal after move; 0 bytes freed until the owner empties the trash). `worktrees/0.6.258` (`4dd1f590`, ancestor of `origin/main`) and `worktrees/0.6.259` (`bcc5fe67`, ancestor of `origin/main`) moved to `$HOME/Desktop/_trash/hub-studio-kh/2026-11-25 0.6.258 worktree/` and `2026-11-25 0.6.259 worktree/` with `MANIFEST.md`, followed by `git worktree prune`. `Hub Studio KH-0.6.259.dmg` promoted to `app/dist/` and verified (`1,483,071,488` bytes, sha256 `7b1542105dadadb14c333b335b2f8bee7a431e49a1af891cf3fff64e4661ee5c`); `app/dist/` keeps 0.6.259 + 0.6.258 (current + previous). Build caches (`app/.build/`) kept untouched; `worktrees/0.6.259/.build/evidence/` preserved in `lead/release-0.6.259-20261124/evidence/build-evidence/`.

## Evidence

- gate log: `lead/gate-20261125-004811-50786/summary.txt`, `step-*.log` beside it; the passing run's full
  console at `lead/release-0.6.259-20261124/gate-run-6.log`
- readback command and output:
  - feed: `feed-verify.sh hub --expect-version 0.6.259 --expect-sha 7b1542105dadadb14c333b335b2f8bee7a431e49a1af891cf3fff64e4661ee5c --public-key <pinned-key>` → `PASS signature — Ed25519 verified`, `published 2026-11-24T20:02:00.067Z`, `PASS`
  - asset: `curl -fsSL -C - -o dl <browser_download_url>` (no auth header), resuming after three connection resets → `bytes=1483071488 sha=7b1542105dadadb14c333b335b2f8bee7a431e49a1af891cf3fff64e4661ee5c`; log at `lead/release-0.6.259-20261124/evidence/anon-readback-0.6.259.log`
- idle footprint: `lead/gate-20261125-004811-50786/step-idle-footprint.log` —
  `idle footprint: pid 73384 = 95 MB after 600s, -28 MB while idle (ceiling 500 MB)`
- install acceptance: UNMEASURED — stopped at the visible-window guard (see `not proven`: `Hub Studio KH.app` and `Studio Launcher KH.app` are absent from `/Applications`, `com.khupdater.agent` is unloaded with no `hub` entry, and `native/StudioHub.swift:1337,1457-1459` calls `openWindow()` + `NSApp.activate(ignoringOtherApps: true)` on `open -g -j` relaunch when `!loginLaunch || !controller.hasLibrary`)
