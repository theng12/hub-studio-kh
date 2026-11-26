# Release receipt — hub 0.6.262

app: hub
version: 0.6.262
commit: fc3964ef018651b24b03a1d338069a7bc3a07f7d — **this is what tag `0.6.262` names** (read from
  `git/ref/tags/0.6.262` → `{"type":"commit","sha":"fc3964ef…"}`), so the tag names the gated commit.
  The appcast is a further commit, `d718c50cc61a9ef7803f0675b6941d989fc707ee`, merged with the rest into
  `main` at `08ba1524` (receipt commit `82b1c516`, amended `6a9f7fc6`); `main` = `origin/main`, pushed.
artifact: `~/Developer/_orchestra/developer-media/hub-studio-kh/app/dist/Hub Studio KH-0.6.262.dmg` — the
  surviving copy, sha256 `d77e196c…`, 1,483,564,032 B, measured in the same command as these figures. It was
  built as `worktrees/release-0.6.262/dist/.candidates/Hub Studio KH-0.6.262.dmg`; that worktree was retired to
  the Desktop trash at closeout, so the `.candidates` path no longer resolves and is not the one to read.
sha256: d77e196c500fe7ecd35b9e3bc8035b11882950ea6d1150eaf66f0800e9f1cad4
bytes: 1483564032
scan: sha256=d77e196c500fe7ecd35b9e3bc8035b11882950ea6d1150eaf66f0800e9f1cad4 clean — `scan-dmg.sh`
  as a gate step, exit 0; OWNER HITS none (the `100.64.0.0/10` tailnet range literal accepted and
  filtered); UNKNOWN none; vendor licence and credit addresses excused by path
gate config: `$HOME/Developer/_orchestra/developer-media/hub-studio-kh/lead/0.6.262/gate-0.6.262.sh`
  (sha256 `3ad7a2b37a32f444…`, frozen into the run's evidence at step 0)
gate exit: 0 — `GATE PASS hub fc3964ef018651b24b03a1d338069a7bc3a07f7d 2026-11-25T21:45:34Z`
gate evidence: `lead/gate-20261126-034418-35876` — 23 steps `ok`, run 7, runner `gate-run.sh 8df512ad14da`.
  `freeze-inputs` hashed 17 inputs at step 0 and `verify-inputs` reported
  **`all 17 gate inputs unchanged since the run started`** at the end, so this green is one run on one
  known set of scripts.
feed readback: **SERVED 2026-11-25T22:24:54.253Z** — `kh-releases` serves `hub` **0.6.262**, sha
  `d77e196c…`, 1,483,564,032 B, `Hub.Studio.KH-0.6.262.dmg`, tagline "Runs AI models on this Mac for your other
  apps."; read back by M001 with `feed-verify.sh hub --expect-version 0.6.262 --expect-sha d77e196c…` → **PASS**.
  S001's pass moved exactly one row (the other 13 byte-identical to `82be58c9`); envelope `0e0b597d…`,
  `kh-releases` `62a5a89a1`. **Released, served and verified.**
page state: **SERVED** — Pages serves `https://theng12.github.io/hub-studio-kh/appcast.xml` with 0.6.262 as
  its first item (0.6.261 second), enclosure `…/download/0.6.262/Hub.Studio.KH-0.6.262.dmg`,
  `length="1483564032"`, and the **served** item's Ed25519 signature verifies against Hub's pinned public key
  over the gated bytes (`appcast-verify.sh` → PASS).
release: non-draft `0.6.262` created `2026-11-25T21:57:33Z`, tag `0.6.262` → **`fc3964ef`** (the gated commit), **exactly 1 asset**
  `Hub.Studio.KH-0.6.262.dmg`, GitHub's stored digest `sha256:d77e196c…` equals the gated sha, anonymous
  request answers `content-length: 1483564032`. No prior release or draft for the tag existed before the
  create (checked first, per the claim rule). The bytes were read from the sealed DMG for the appcast:
  `CFBundleShortVersionString 0.6.262`, `SUFeedURL https://theng12.github.io/hub-studio-kh/appcast.xml`,
  and its `SUPublicEDKey` equals the key the signature verified against.
  **Anonymous download of the published asset complete:** 1,483,564,032 B and sha256 `d77e196c…` — the same bytes,
  and the served appcast signature verifies against that downloaded file, not only against the local one.
dirty: false
idle footprint: **125 MB after 600 s, 1 MB lower than at start**, ceiling 500 MB — the gate's own
  `idle-footprint` step started the staged build's Hub worker (`hub_worker.py`) hidden, temp data dir
  and temp `HOME`, health 200, then the 600 s window. Same window, live routes on the staged app:
  `/api/hub/model-packages` and `/api/hub/model-presets` — both with zero `unknown` rows.
retired: none — 0 bytes. Nothing removed from `dist/`; the candidate DMG stays in `.candidates`.
not proven: (1) an installed 0.6.262 on any Mac — the feed now offers it, but no Mac has been read back
  running it; (2) whole-fleet and long-run idle-footprint
  states — only idle-after-one-cycle was measured; (3) archive, load-image, deploy, SPM and testKit
  tests do not run on this host; xcodebuild itself is present; (4) the app tests not selected by
  `suites` (the gate runs 15 of 324 top-level `test_*.py` files — see "Suite scope" below).

## What this release changes

`packaging/make_dmg.py` no longer parks its staging copy in the project. A finished build staged in
`<repo>/.build`, then moved that whole tree into `<repo>/_trash/dmg-scratch` and kept it — 8.5 GB after
one gate (issue row `20261125-49`). It now stages under the system temp folder
(`tempfile.mkdtemp(prefix='hub-dmg-stage-')`, no `dir=`), removes that copy on success, and on failure
keeps it in temp and prints its path, never moving it into the checkout. The replaced-artifact archive
(`preserve`) is unchanged, so a superseded installer is still kept recoverably.

`tests/test_sealed_manifest_packaging.py` filed its throwaway DMG into `<repo>/_trash/test-dmgs` — the
same checkout trash, reached through a second door. It now removes its own artifact.

`tests/test_safe_dmg_packaging.py` gains `test_a_finished_build_leaves_nothing_staged_in_the_checkout`:
staging is inside the system temp folder, not inside the checkout, removed when the build completes,
and no `_trash` appears. VERSION `0.6.262` and its changelog entry are in the same commit, so the
installer carries the version being gated.

## The two proofs this release exists for

1. **`dmg` — a real build leaves nothing behind.** `worktree size before build: 7,861,600 KB; after:
   9,310,396 KB; growth: 1,448,796 KB`, which is the staged bundle being read into a 1.48 GB image;
   and `checkout _trash before build: none; after: none`. The step fails on any checkout `_trash` and
   on a multi-GB jump, so this is a measurement, not a claim.
2. **`failed-build` — the failure path, for real.** The output folder is made non-writable, so
   `diskutil image create` genuinely fails: `interpreter: …python3.13 3.13.18`,
   `failed-build staging path: /var/folders/…/T/hub-dmg-stage-ajw3gai6`, `OK: under the system temp
   folder`, `OK: kept for this run, as designed`, `OK: failed build left nothing in the checkout`. The
   step requires the `CalledProcessError` from `diskutil`, so a fixture that stops earlier cannot pass.

## Suite scope — the gate does not run every test

`suites` runs 15 modules by hand, not the tree. 13 unittest modules — `test_safe_dmg_packaging` (5
tests, OK), `test_sealed_manifest_packaging` (15 tests, OK), `test_packaging_trash_archive` (2, OK),
`test_model_needs`, `test_hub_model_packages`, `test_hub_model_presets_route`,
`test_hub_modality_vocabulary`, `test_model_presets`, `test_model_manifest_catalog`,
`test_model_install_plan`, `test_model_packages`, `test_model_download_queue`,
`test_hub_ops_visibility` — **259 unittest tests**, and 2 pytest modules (`test_fleet_model_install` 19 passed,
`test_lan_model_share` 21 passed). `failed=0`.

**Wider gap, carried forward and not closed:** `tests/` holds 324 top-level `test_*.py` files; 15 run
here.

## Run history — every attempt, kept as what it was

| # | Evidence | Result | Cause | Fix |
|---|---|---|---|---|
| 1 | `gate-20261126-003746-8590` | FAILED `suites` | Row `20261126-1`: every suite ran with a temp `HOME`, which hides the login keychain; `security find-identity` returned nothing and `stage_voice` refused to sign. `test_safe_dmg_packaging` 1 error, `test_sealed_manifest_packaging` 10 errors. The other 11 suites and both pytest modules were green. | The two signing suites run with the real `HOME` and keep a temp `STUDIOHUB_DATA_DIR`; measured before/after: temp `HOME` → 1 + 10 errors, real `HOME` → 20 tests OK. Each suite's log line names its `HOME`. |
| 2 | `gate-20261126-004645-12031` | FAILED `failed-build` | The fixture wrote its DMG outside `dist/.candidates`, so `build()`'s first guard raised `ValueError: DMG output must be in this project dist or dist/.candidates folder` before `diskutil` — the step never reached the path it exists for, and printed only the exception type. Reproduced deterministically. | Output inside the scratch `dist/.candidates`, folder made non-writable so `diskutil` fails for real; the log prints the message and the interpreter; the step requires `CalledProcessError`. |
| 3 | `gate-20261126-005828-16259` | FAILED `update-rule` | Row `20261126-2`: the proof script pinned `ROOT=…/worktrees/release-0.6.261`, retired with 0.6.261's closeout, so `sign_update` was absent. Everything before it green. | The script resolves its checkout (`release-0.6.262` → `release-0.6.261` → `app`) and refuses with a sentence when none carries `sign_update`; copied into `lead/0.6.262/` so a running release does not depend on the previous release's folder. |
| 4 | `gate-20261126-013900-67228` | GATE PASS, all 21 steps | **Not accepted.** The config and `update-rule-proof.sh` were edited at 01:38:32 during the run (started 01:38:00), so a step executed script text that changed under it (row `20261123-155`). Nothing was falsely passed; the green cannot be repaired into one. | Inputs are hashed at step 0 and re-checked at the end. |
| 5 | `gate-20261126-024417-10824` | FAILED `verify-inputs` | The freeze wrote 17 hashes; the verifier read `$FROZEN` set in another step's subshell, got an empty string, ran `shasum -c ''` and failed a run whose 17 inputs were verified unchanged by hand. Its error redirect also created `.check` in the worktree (row `20261126-15`), which refused run 6 as dirty. | `verify_inputs` re-derives the path, refuses when the list is empty, and writes its output to an absolute path in the evidence dir. `freeze_inputs` validates every input exists before writing anything. |
| 6 | (refused before a run dir) | GATE REFUSED: tree not clean | Run 5's stray `.check` in the worktree. The guard worked; no gate result. | The stray file was removed; the writer can no longer produce a relative output path. |
| 7 | `gate-20261126-034418-35876` | **GATE PASS** at `fc3964ef`, 23 steps | — | **This is the run this receipt cites.** |

## Unproven / open items carried forward

- Replace-and-relaunch of the updater stays unproven from 0.6.261; this release does not change it.
- `update-rule` and `hold-proof` are 0.6.261's scripts, copied into `lead/0.6.262/`; they exercise the
  staged bundle, not new code in this release.
- The suite-scope gap above (15 of 324).
- The staging fix is proved for the build path and the test path; a future path that calls `preserve()`
  directly is still responsible for not passing a checkout path.

## Evidence

- gate log: `lead/gate-20261126-034418-35876/summary.txt`, `step-*.log` beside it,
  `frozen-inputs.sha256` + `frozen-inputs.check`
- `dmg`: `step-dmg.log` (size before/after, `_trash` none/none, sha256, bytes, `hdiutil verify`)
- `failed-build`: `step-failed-build.log` (interpreter, staging path, three OK lines)
- `suites`: `step-suites.log` (module list, per-module rc, `failed=0`)
- feed row: `feed-verify.sh hub --expect-version 0.6.262 --expect-sha d77e196c…` → **PASS**, run
  2026-11-26 against the live feed and quoted at its `published_at 2026-11-25T22:24:54.253Z` (14 rows).
- appcast: `https://theng12.github.io/hub-studio-kh/appcast.xml` read at the same time — 0.6.262 is the
  first item, `length="1483564032"`; its `edSignature` verified with `appcast-verify.sh` against Hub's
  pinned public key over an anonymous download of the published asset (same sha, same length).
