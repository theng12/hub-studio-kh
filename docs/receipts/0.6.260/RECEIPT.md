# Release receipt — hub 0.6.260

app: hub
version: 0.6.260
commit: bf66c16af7e79e8316a7084b1cc0de65d055b445 (built, and pushed to `origin/main` unchanged —
  no branch/merge step; the artifact was built from this commit in the canonical checkout
  `hub-studio-kh/app`, branch `main`)
artifact: `$HOME/Developer/_orchestra/developer-media/hub-studio-kh/app/dist/Hub Studio KH-0.6.260.dmg`
sha256: 8cbee1667af568c3fbbace07d044027bd5dfedb0331501cfa8a5e54161211a81
bytes: 1484369920
scan: sha256=8cbee1667af568c3fbbace07d044027bd5dfedb0331501cfa8a5e54161211a81 clean — `scan-dmg.sh`
  scan-dmg/2026-11-20, exit 0, 67,272 files, 443 s; OWNER HITS none (the `100.64.0.0/10` range literal
  is the accepted, filtered tailnet range); UNKNOWN none; vendor licence and credit addresses excused
  by path
gate config: `$HOME/Developer/_orchestra/developer-media/hub-studio-kh/lead/release-0.6.260-20261125/gate-0.6.260.sh`
gate exit: 0 — `GATE PASS hub bf66c16af7e79e8316a7084b1cc0de65d055b445 2026-11-25T07:01:25Z`
gate evidence: `lead/gate-20261125-124644-24497` (run 2). Runner `gate-run.sh 528de9fe53d0`.
feed readback: asset anonymous readback **PASS** — published as GitHub release `0.6.260` on
  `theng12/hub-studio-kh`; stored asset `Hub.Studio.KH-0.6.260.dmg` at
  `https://github.com/theng12/hub-studio-kh/releases/download/0.6.260/Hub.Studio.KH-0.6.260.dmg`;
  a fresh `curl -fsSL` with no auth header returned **HTTP 200, 1,484,369,920 bytes, sha256
  8cbee1667af568c3fbbace07d044027bd5dfedb0331501cfa8a5e54161211a81** — equal to the gated artifact
  and the scan receipt. The served **row** on `kh-releases` is written by the feed lane's `khu feed`
  producer (RELEASE-POLICY §"One producer"); this release hands it: version `0.6.260`, stored file
  `Hub.Studio.KH-0.6.260.dmg`, sha256 `8cbee166…1a81`, bytes `1484369920`, tagline and description
  from `app-meta.json` at the tag
page state: UNMEASURED — the feed row precedes the page comparison and is the feed lane's pass
dirty: false
idle footprint: **92 MB after 600 s, 33 MB lower than at start**, ceiling 500 MB — the gate's own
  `idle-footprint` step started the staged build's Hub worker (`hub_worker.py`), hidden, temp data
  dir and HOME, health 200, then the 600 s window. In the same window it read the staged app's live
  routes: `/api/hub/model-packages` 41 models (`declared` 37, `measured` 4, `unknown` 0) and
  `/api/hub/model-presets` 37 models (`declared` 17, `measured` 20, `unknown` 0).
retired: `Hub Studio KH-0.6.258.dmg` (1,483,356,160 bytes, sha256
  `81c1adf460e3df84550e34a8f0d63453603017c7c11fc61d4d4ea2a2b36d2c64`) moved to
  `$HOME/Desktop/_trash/hub-studio-kh/2026-11-25 0.6.258 installer/` with `MANIFEST.md`; size and
  sha256 verified equal after the move. 0 bytes freed until the owner empties the trash.
  `app/dist/` now holds exactly 0.6.260 + 0.6.259 (`check-dist-retention.sh`: `KEEP … 0.6.260`).
not proven: (1) a served-feed row for this version (the feed lane's producer pass, not this lead's);
  (2) an installed 0.6.260 serving `needs_gb` on this Mac (the Launcher's automatic install follows
  the feed row); (3) a live runtime upgrade of a model's figure from `declared` to `measured` — the
  mechanism is unit-tested with its history, no real refusal or load has occurred since;
  (4) whole-fleet and long-run idle-footprint states — only idle-after-one-cycle was measured

## Gate runs — every attempt, kept as what it was

| # | evidence | outcome |
|---|---|---|
| 1 | `gate-20261125-123703-11647` | **FAILED `build-inputs`** — `.build/yue2-runtime` was absent from the canonical `.build/`, so the recipe rebuilt it and then tried to move the read-only tree through the checkout's `_trash` symlink: `mv: rename … Permission denied`, and the tree it built digested `6478f0e9…` against the pin `e2e4d1df…`. Fixed by restoring the qualified tree from the retired 0.6.259 worktree (digest verified equal to the pin) and re-running `packaging/rebuild_build_inputs.sh --root .` alone, which then reported all four inputs present. Durable fix queued for 0.6.261 (refuse a checkout `_trash` symlink, `chmod u+w` before any move, retire by absolute Desktop path) |
| 2 | `gate-20261125-124644-24497` | **GATE PASS** at `bf66c16a`; artifact recorded with dev/ino/size/mtime/ctime identity; scan clean |

## Tests run in the passing gate

`tests.test_model_needs` 6 · `test_hub_model_packages` 13 · `test_hub_model_presets_route` 21 ·
`test_hub_modality_vocabulary` 48 · `test_model_presets` 23 · `test_model_manifest_catalog` 26 ·
`test_model_install_plan` 5 · `test_model_packages` 81 · `test_model_download_queue` 8 ·
`test_hub_ops_visibility` 6 · pytest: `test_fleet_model_install` 19, `test_lan_model_share` 21 —
**0 failed**. Native boundary, API, secret-masking, view and lifecycle checks passed inside
`native/build.sh`. Suites peak 208 MB over 42 s.

## What this release changes

Every model Hub offers now carries `needs_gb` (GB of RAM needed to run) and `needs_source`
(`declared` | `measured`, never `unknown`) on `/api/hub/model-packages`, `/api/hub/model-presets`,
`/api/hub/catalog` and `/api/hub/models`. `declared` is the model's shipped bytes plus a constant
per-engine runtime overhead (`components/hub/backend/model_needs.py`: 0.5 GB `embed-mlx` /
`voice-vits`; 1.0 GB `subtitle-mlx` / `vision-mlx` / `voice-mlx`; 2.0 GB `chat-mlx` / `image-mflux`;
2.5 GB `music-yue2`; 3.0 GB `image-mlx-serve` / `music-mlx`). `measured` replaces it from a real
worker refusal (`note_refusal`, wired at the capacity hand-back), a real load (`note_load`), the
manifest's own `qualification.min_free_memory_gb`, or the worker's published free-memory floor, and
keeps every replaced value in a bounded history. A model no Mac can run stays listed with its
number and its shortfall.

## Evidence

- gate log: `lead/gate-20261125-124644-24497/summary.txt`, `step-*.log` beside it; the passing run's
  full console at `lead/release-0.6.260-20261125/gate-run-2.log`, the failed run at `gate-run-1.log`
- idle footprint: `lead/gate-20261125-124644-24497/step-idle-footprint.log` —
  `idle footprint: pid 96189 = 92 MB after 600s, -33 MB while idle (ceiling 500 MB)` plus the two
  live route lines quoted above
- readback: `lead/release-0.6.260-20261125/publish.log` (stored asset name, download URL, anonymous
  download bytes and sha256) and `evidence/release-0.6.260.json`
- install acceptance: UNMEASURED — follows the Launcher's automatic install once the feed serves this version
