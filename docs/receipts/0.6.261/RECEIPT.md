# Release receipt — hub 0.6.261

app: hub
version: 0.6.261
commit: f9f1ad00e4c60ef611b77f702b80340ca0bc5db5 (built and gated in the release worktree
  `hub-studio-kh/worktrees/release-0.6.261`, branch `release/0.6.261`, pushed at that commit; merged
  `--no-ff` into `main` = `6dc7321a` by the Developer, pushed — this receipt commits on that main)
artifact: `$HOME/Developer/_orchestra/developer-media/hub-studio-kh/worktrees/release-0.6.261/dist/.candidates/Hub Studio KH-0.6.261.dmg`
sha256: 6cc3baa80d789d435afbc92e2c4e7c3232a2803fe1391cb5cc20f2577b1060af
bytes: 1483807744
scan: sha256=6cc3baa80d789d435afbc92e2c4e7c3232a2803fe1391cb5cc20f2577b1060af clean — `scan-dmg.sh`,
  exit 0, 67,357 files, largest 386 MB, 477 s wall; OWNER HITS none (the `100.64.0.0/10` tailnet range
  literal is accepted and filtered); UNKNOWN none; vendor licence and credit addresses excused by path
  (bundled ffmpeg/ffprobe)
gate config: `$HOME/Developer/_orchestra/developer-media/hub-studio-kh/lead/0.6.261/gate-0.6.261.sh`
gate exit: 0 — `GATE PASS hub f9f1ad00e4c60ef611b77f702b80340ca0bc5db5 2026-11-25T14:10:17Z`
gate evidence: `lead/gate-20261125-200645-47681`, 21 steps all `ok`. Runner `gate-run.sh 528de9fe53d0`,
  heavy lane, declared normal, 12,891 MB available at start.
feed readback: asset anonymous readback **PASS** — published as GitHub release `0.6.261` (non-draft,
  id `458229393`) on `theng12/hub-studio-kh`, tag `0.6.261` = `f9f1ad00e4c60ef611b77f702b80340ca0bc5db5`;
  stored asset `Hub.Studio.KH-0.6.261.dmg` at
  `https://github.com/theng12/hub-studio-kh/releases/download/0.6.261/Hub.Studio.KH-0.6.261.dmg`;
  a fresh `curl -sIL` with no auth header answered **HTTP 200, `content-length: 1483807744`**, and
  GitHub's own asset `digest` field reads
  `sha256:6cc3baa80d789d435afbc92e2c4e7c3232a2803fe1391cb5cc20f2577b1060af` — the server hashed the
  bytes it received, and they equal the gated artifact. The served **row** on `kh-releases` is the
  feed lane's (RELEASE-POLICY §"One producer"); this release handed S001: version `0.6.261`, stored
  file `Hub.Studio.KH-0.6.261.dmg`, sha256 `6cc3baa8…60af`, bytes `1483807744`. The row's tagline is
  the tag's own (`app-meta.json` at `0.6.261`: "Runs AI models on this Mac for your other apps."),
  which wins over any registry text; the lead's suggested sentence is not used for the row.
feed served: **PASS** — `published_at 2026-11-25T15:06:49.188Z`, 28,671 feed bytes, served `hub` row
  `0.6.261` / sha256 `6cc3baa8…60af` / 1,483,807,744 B / tagline "Runs AI models on this Mac for your
  other apps.", verified by M001 with `feed-verify.sh --expect-version 0.6.261 --expect-sha
  6cc3baa8…60af` → `PASS signature` (Ed25519 against the pinned feed key). S001 reported the same
  `published_at` and an envelope sha256 `3303a8a6…9a07`; `printer-studio 1.0.46` moved in the same pass.
page state: same — Pages serves `appcast.xml` with one item: `shortVersionString` `0.6.261`,
  `length="1483807744"`, enclosure URL exactly the asset URL above, `edSignature`
  `MXqjYaIQGlngGSsJY5I/Jjh8H6GcgFPJfm4N0Bmw+5nEJhAHczuwVfPZLwJEcBJogpZIab7P7+ZZmJUQCIyAAA==`.
  Item committed `922f2452` on `main` `6dc7321a`; read back anonymously at 2026-11-25 by this lead.
dirty: false
idle footprint: **126 MB after 600 s, +1 MB while idle**, ceiling 500 MB — the gate's own
  `idle-footprint` step started the staged build's Hub worker (`hub_worker.py`) hidden, on a temp data
  dir and temp `HOME`, health 200, then the 600 s window. In the same window it read the staged app's
  live routes: `/api/hub/model-packages` 41 models (`declared` 37, `measured` 4, `unknown` 0) and
  `/api/hub/model-presets` 37 models (`declared` 33, `measured` 4, `unknown` 0).
  **The runner's generic line is not this measurement:** `summary.txt` also carries
  `step idle-footprint :: UNMEASURED (set IDLE_PID in the gate config to measure it)`, which is
  `gate-run.sh` reporting that the config names no `IDLE_PID` — a known defect, row `20261125-16`.
  The 126 MB line is the config's own step and is the number this receipt carries.
retired: `Hub Studio KH-0.6.259.dmg` — 1,483,071,488 B, sha `7b154210…6e5c` — moved to
  `$HOME/Desktop/_trash/hub-studio-kh/2026-11-25 0.6.259 installer/` with a `MANIFEST.md`; size and sha
  re-verified equal after the move. `dist/` keeps current + previous: **0.6.261 + 0.6.260**. The release
  worktree and its 8.5 GB of DMG staging (row `20261125-49`) retire after this receipt; `make_dmg.py`
  no longer preserves staging (0.6.262, `af123191`), so the next gate will not add to it.
not proven: (1) **replace-and-relaunch unproven** — it lands with the first real served update; what
  was proved on this build is hold → found → download → safe abort → resume; (2) an installed
  0.6.261 on the shipped machine (the Launcher's automatic install follows the feed row, and the row
  is served now);
  (4) whole-fleet and long-run idle-footprint states — only idle-after-one-cycle was measured;
  (5) a real `declared → measured` upgrade of a model's RAM figure from a refusal or a load — the
  mechanism and its history are unit-tested, no real refusal or load has occurred since.

## The duplicate draft — what happened, plainly

Two sessions published this version in the same minutes, each under the owner's own approval in its
own session. GitHub shows two objects: **`458229393`** (14:30:02Z), the one that survived and holds the
1.4 GB asset, and **`458232180`** (14:32:07Z), an **empty draft with zero assets**, deleted in the
same pass by the session that published the release. Nothing was lost and nothing burned: the deleted
object never held an asset.

The two creates are two minutes apart (14:30:02Z and 14:32:07Z), and one `gh release create`
invocation creates one release, so two objects means two invocations in the same minutes — not a
single command that abandoned its own draft. Which run created which object cannot be proved from
GitHub, because both runs pushed as the same account. What each run observed: the lead's `gh release create` printed
`HTTP 404 … /releases/458232180/assets` and `cleaning up draft failed` mid-run, naming the empty
object; the Developer's run reports creating `458229393`. The lead did not retry, because a retry is
how a second release appears.

**A duplicate that hides a failed attempt reads as intent**, so it is written here rather than left to
a later reader to guess. The state that stands after this release: exactly one release for `0.6.261`,
one asset, non-draft, digest equal to the gate's artifact line.

## Gate runs — every attempt, kept as what it was

| # | evidence | outcome |
|---|---|---|
| 1 | `gate-20261125-200645-47681` | **GATE PASS** at `f9f1ad00`; artifact recorded with its sha256 and bytes; scan clean; 21 steps `ok` |

No failed run. Two config corrections were made before the run and are visible in the config's own
comments: `EXPECTED_HEAD` was 0.6.260's commit and now names `f9f1ad00`; `SEALED_DMG` is 0.6.260 (the
previous shipped artifact the kit descriptor is read from), annotated as such. The voice/image rebuild
steps were removed before the run because they would have produced trees the models were not qualified
on: staging reads the primary checkout's qualified runtimes (image `a08b5852…`, yue2 `e2e4d1df…`, both
equal to the pins), and preflight only checks that those trees **exist** — `stage_voice` is what
compares their digests, and it did.

## Tests run in the passing gate

`tests.test_model_needs` 6 · `test_hub_model_packages` 13 · `test_hub_model_presets_route` 21 ·
`test_hub_modality_vocabulary` 48 · `test_model_presets` 23 · `test_model_manifest_catalog` 26 ·
`test_model_install_plan` 5 · `test_model_packages` 81 · `test_model_download_queue` 8 ·
`test_hub_ops_visibility` 6 · pytest: `test_fleet_model_install` 19, `test_lan_model_share` 21 —
**0 failed**. Native boundary, API, secret-masking, view and lifecycle checks passed inside
`native/build.sh`. Suites peak 201 MB over 42 s; native peak 12,740 MB over 278 s.

## The update rule, proved on this build

Hidden, real key, real Hub, real queued import (`pocket-tts-tokenizer`), on the staged bundle:
hold taken at 1 s → the updater finds a genuinely signed item (0.6.262 fixture) → `state 1` → download
starts → **the swap aborts safely** (the payload is deliberately not an installable archive) →
`state 0` → the hold is released by itself with the queued work still there → **0 windows on screen**
over 90 s and 150 s of sampling. That is the hold-and-settle half. **Replace-and-relaunch was never
exercised** and is the unproven item above.

Hub-side hold, proved separately: real queued import → `POST /api/maintenance/hold?timeout_s=20&pause_s=30`
→ `held dispatch paused 30.0s, in_flight=0, safe=True` → the run survives → `DELETE` →
`resumed dispatch_paused_s=0`. Shim present inside the bundle.

## What this release changes

Hub Studio KH ships its own updater: Sparkle 2.10.1 through the shared KH shim
(`vendor/khsparkle/`, pinned, `shim-pin.sh --check` MATCH), its own Ed25519 key (only the public half
is in the tree), `SUFeedURL`/`SUPublicEDKey`/`KHHoldURL`/`KHHoldTokenFile` in the bundle's plist,
`CFBundleVersion` tracking the version, and the framework copied into `Contents/Frameworks` before
signing with the library-validation exemption. The updater starts after the app's local services are
ready (cold-start wait, 120 s bound, refusal logged), launches as an accessory with no window, and
appends `.updater` to the quit source. The appcast item is built from the mounted DMG's own bundle
version and its exact bytes, never from the checkout.

It also carries the build-inputs repair from `d4166203`: the recipe retires a stale input to the
Desktop trash by its real path, refuses a `_trash` symlink inside a checkout, opens a sealed tree
before moving it, and no longer files a fresh build as the old one.

## Evidence

- gate log: `lead/gate-20261125-200645-47681/summary.txt`, `step-*.log` beside it
- idle footprint: `lead/gate-20261125-200645-47681/step-idle-footprint.log` —
  `idle footprint: pid 2932 = 126 MB after 600s, +1 MB while idle (ceiling 500 MB)` plus the two live
  route lines quoted above
- update rule: `lead/gate-20261125-200645-47681/step-update-rule.log` (the PASS lines and the
  `kh-sparkle: state 1` / `state 0` beats); hold: `step-hold-proof.log`
- appcast: `lead/gate-20261125-200645-47681/step-appcast-item.log`
  (`PASS item names this artifact version 0.6.261, 1483807744 bytes`,
  `PASS signature SIGNATURE OK over the DMG that is mounted`); the served item read back from
  `https://theng12.github.io/hub-studio-kh/appcast.xml`
- readback command and output: `curl -sIL https://github.com/theng12/hub-studio-kh/releases/download/0.6.261/Hub.Studio.KH-0.6.261.dmg`
  → `HTTP/2 200`, `content-length: 1483807744`; `gh release view 0.6.261 --json assets` → one asset,
  `state: uploaded`, `size: 1483807744`, `digest: sha256:6cc3baa8…`
- install acceptance: UNMEASURED — follows the Launcher's automatic install once the feed serves this
  version
