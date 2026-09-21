# Studio Hub: new-computer handoff

Prepared 2026-09-20. Start with this file and [the current board](tasks/BOARD.md).
This is the continuation guide for the existing canonical plan, not a second plan.

## Current continuation — 2026-09-21

**Latest:** PR #12 merged at `b550617`; R01 native lifecycle/rejoin and R02 reference
validation are verified. The exact wave-1 candidate passed 632 root tests (seven
skips); see [wave-1 evidence](../qualification/handoff-completion-wave1-2026-09-21.md).
The owner now names **GatewayKH** as the external client instead of GenStudio.
R03 Voice receipt/payload repair is in progress; follow the top board block and
canonical R01–R10 plan. The older continuation below is provenance, not a request
to redo completed native/reference repairs. No GatewayKH live connection is implied.

[MEASURED] The audit repairs H-01 through H-05 are integrated, followed by the
reviewed Music package/engine contract through
[PR #10](https://github.com/theng12/Studio-Hub-KH/pull/10) at `9f25c03`.
The runtime continuation is reviewed at `c9eadca`, with integration tracked by
[PR #11](https://github.com/theng12/Studio-Hub-KH/pull/11). Its full local gate
passes 631 tests (7 skips), nested Voice 722/19 and Hub 85. Consult the
[resume receipt](../qualification/new-computer-resume-2026-09-21.md) for the clean
artifact identity and next native lifecycle/reference-validation tasks, and the
[Music recovery card](tasks/MUSIC-RECOVERY.md) for its runtime, staged-source,
supervision, routing, and no-model startup evidence. The older baseline and
transfer inventory below remain provenance and setup reference, not a request to
restart the recovery from the preserved draft.

## What is safe to continue from

[MEASURED] The private GitHub repository is **https://github.com/theng12/Studio-Hub-KH**.
Its default/integration branch is **`implementation/voice-runtime`**. The audited
source baseline is **`f6362e0b5dcb71231d8023f9e9cbc06be0a55267`**, which merges the
reviewed cleanup repair through [PR #1](https://github.com/theng12/Studio-Hub-KH/pull/1).
Use the latest integration tip containing this handoff; do not reset to an older SHA.

[MEASURED] Handoff content commit: `c4909a64e8178a5e24dcfc2503cf9b539f65057a`.
[PR #2](https://github.com/theng12/Studio-Hub-KH/pull/2) records its integration and
the final merge commit. This link also distinguishes the reviewed handoff from an
uncommitted draft on the old machine.

[MEASURED] Every pre-existing local branch tip was compared with GitHub and matched,
including the newly uploaded **`codex/recovered-music-draft`** at
**`0eebc424f8a74b2b95ec4f9b90fccb32aae0f718`**. Exact refs and environment receipts are in
[repository-state.json](../qualification/evidence/handoff-2026-09-20/repository-state.json).
The Music branch is preserved WIP, **not merged**. Its own
`docs/tasks/MUSIC-RECOVERY.md` records the interrupted files and next contract task.

[MEASURED] All nine other registered worktrees have clean Git status; their ignored
build/model artifacts remain local. The main checkout's pre-existing untracked item
is `.serena/`; the other changes at audit time are this handoff. See the
[worktree snapshot](../qualification/evidence/handoff-2026-09-20/worktree-state.json).

[MEASURED] The project has a working development baseline, with remaining defects and
qualification gaps. [The audit backlog](../qualification/bug-audit-2026-09-20.md)
contains triggers, source evidence, suggested fixes and acceptance checks. This audit
does not establish that every possible bug has been found.

| Evidence label | Meaning in this handoff |
| --- | --- |
| [MEASURED] | Direct source inspection, command output or reproduction checked by the coordinator; scope is stated. |
| [REPORTED] | Historical or worker statement not independently established here. |
| [ASSUMED] | A gap filled explicitly to proceed; never hardware qualification or authority. |

## Source, installed app and proof bundle are different

[MEASURED] `VERSION` is **0.6.38**. The old computer's installed app at
`~/Applications/Studio Hub.app` contains **`415df68`**, before the cleanup repair.
It started and returned authenticated health 200/true during takeover, but it has
not been replaced. Do not use its version number alone to decide whether a fix is present.

[MEASURED] A separate repair proof exists on the old computer at
`/build/.codex/worktrees/studiohub-takeover-repair/studiohub-standalone/dist/Studio Hub.app`.
It records 0.6.38, base `415df68`, and `development_build: true`. Changed module hashes
were checked against repaired source. It is an explicitly identified local proof,
not a clean release artifact. Its adjacent tree hash is recorded in
[the takeover evidence](../qualification/takeover-2026-09-20.md).

[MEASURED] The latest full gate on the repaired source passed **584 root tests with
7 skips**, including **722 Voice passes / 19 skips** and **54 Hub regression passes**.
The native build and isolated packaged API/cleanup/shutdown probe passed. These counts
are from the prior takeover in this session, not a newly repeated full run for this
documentation pass. Product source is unchanged by this handoff.

[MEASURED] The new audit probes deliberately fail on the audited source and live
outside ordinary `tests/` discovery. A green existing suite therefore does not close
the audit backlog. No new real-model inference or physical fleet test was run here.

## Obtain the source on the new computer

The private repository requires an account with access. Authenticate Git/GitHub on
the new computer, then run:

```sh
mkdir -p "$HOME/Projects"
cd "$HOME/Projects"
git clone https://github.com/theng12/Studio-Hub-KH.git studiohub-standalone
cd studiohub-standalone
git switch implementation/voice-runtime
git pull --ff-only
git status --short --branch
git log -1 --format='%H %s'
git branch -r
```

For inspection of the recovered draft, use an independent checkout:

```sh
git worktree add -b codex/resume-music ../studiohub-music origin/codex/recovered-music-draft
```

Do not merge that entire draft into the integration branch. First read its recovery
card and the Music finding in the audit. The main catalog must remain valid while
the new contract is implemented. The recovered branch starts before the cleanup repair.

[MEASURED] Old `.worktrees/` directories, the recovery stash, `.build/`, `dist/` and
`.serena/` do not transfer through a clone. The stash's Music source is already
committed on GitHub; restoring that stash over the integration branch is unnecessary.
The previous machine's `icloud` remote is a local filesystem path, not a usable
remote on a fresh computer. Source reference paths in `research/source-checkouts.json`
are provenance; the adopted component source is already in this repository.

## Transfer inputs, or rebuild them

[MEASURED] The observed old-machine paths and sizes are recorded in the audit's
transfer inventory. The critical inputs are:

| Old-machine input | Destination/purpose | Treatment |
| --- | --- | --- |
| `<old repo>/.build/voice-runtime/` | `<new repo>/.build/voice-runtime/`; Python and Voice/Subtitle/Hub dependencies | Copy the complete standalone immutable runtime with relative symlinks and permissions, then verify its manifest; or rebuild. |
| `<old repo>/.build/st03-image-runtime-worker/` | Same relative destination; isolated Image dependencies | Copy the actual runtime directory, then verify; or rebuild into this name. |
| `<old repo>/.build/test-deps/` | Same relative destination; pytest and its dependencies | Copy or recreate from the observed test dependency list below. |
| `~/StudioHubModels/` | Prefer `~/StudioHubModels/` on the new Mac | Transfer selected complete manifest-defined packages and their companion assets/receipts; validate against tracked manifests. Never substitute a whole Pinokio cache. |
| `<old repo>/.build/downloads/` and build caches | Optional runtime rebuild inputs | Preserve if avoiding downloads matters; these are not source or installed app state. |
| Repair proof app plus adjacent `.app.tree-sha256` | Optional comparison artifact | Keep its development identity; rebuilding from the current clean source is a separate step. |

Do not copy `.build` symlinks from the old repair worktree as if they were runtimes:
they point back into the old main checkout. Copy the actual standalone runtime roots.
Leave old worktrees and artifacts intact until the new machine passes intake checks.

Do not blindly transplant `~/Library/Application Support/StudioHub Standalone`,
preferences, credentials, fleet identity, live job databases, or service registration.
Start isolated development state on the new computer. Export any personal reference
audio or wanted outputs deliberately using the app's supported flows; that is separate
from restoring source. No personal-state migration was requested or performed here.

### Verify copied runtimes and models

Run these read-only runtime checks from the new repository root after placing the
inputs. They do not install models or enroll the computer:

```sh
PY="$PWD/.build/voice-runtime/bin/python3"
"$PY" --version
"$PY" -I -B packaging/voice-runtime/runtime_packaging.py verify .build/voice-runtime
"$PY" -I -B packaging/voice-runtime/runtime_packaging.py verify .build/st03-image-runtime-worker
```

**Register/repair the copied model library (writes its installation record):**
The next command scans complete packages and can update `.studiohub/installed.json`
in the selected library. Run it only after copying the intended new-machine packages.

```sh
"$PY" -B components/model_packages.py scan --library "$HOME/StudioHubModels" --manifest model-manifests
```

Inspect every scan result; command exit alone is not proof that every model is
installed/ready. Package completeness and successful inference are separate checks.
The scan can register verified folders in the selected library, so use the intended
new-machine library rather than an unrelated live cache.

[MEASURED] The old test dependency directory contains the following exact versions,
read from installed `METADATA`, recorded in `repository-state.json`:
`pytest==9.1.1`, `iniconfig==2.3.0`, `packaging==26.3`, `pluggy==1.6.0`,
`Pygments==2.21.0`. Recreating it requires `uv` and access to these packages through a package source
or populated cache; neither has been checked on the new computer. Use a
development-only target directory:

```sh
uv pip install --python "$PY" --target .build/test-deps \
  pytest==9.1.1 iniconfig==2.3.0 packaging==26.3 pluggy==1.6.0 Pygments==2.21.0
```

This reconstruction command has not been exercised on the new computer. Do not
install test tools into the immutable shipped runtime or product dependencies.

### Rebuilding instead of transferring

[MEASURED] Runtime builders require Apple Silicon macOS and developer tools. They
refuse existing output directories. Voice downloads checksum-pinned archives and
installs the lock; Image requires the pinned CPython archive already cached and
uses `uv --offline`. Prefer transferring the verified Image runtime until the exact
Image lock dependencies are available in a declared cache. The command below
explicitly selects the transferred project cache, but that cache has not been proven
to contain every Image dependency. Check/populate it first; an offline cache miss is
a setup blocker, not an app defect. A clean clone alone is not an offline rebuild kit.

```sh
bash packaging/voice-runtime/build.sh
UV_CACHE_DIR="$PWD/.build/uv-cache" bash packaging/image-runtime/build.sh .build/st03-image-runtime-worker
```

Do not delete an existing runtime just to retry these commands. Resolve the stated
prerequisite or use a new output name and qualify the new artifact. Download endpoint
availability and a fresh runtime build were not tested in this audit.

## First development checks

`native/build.sh` and `native/render-page.sh` share explicit SDK selection, defaulting
to the tested `macosx26.5`. Before compiling, inspect the new toolchain:

```sh
uname -m
sw_vers
xcode-select -p
xcrun --sdk macosx26.5 --show-sdk-path
```

If that SDK is unavailable, select an installed compatible SDK explicitly with
`STUDIOHUB_MACOS_SDK=<xcrun-sdk-selector>` on either script, then run the complete
native gate. Empty/unavailable selectors fail before compilation; no newest-SDK
fallback is attempted. `build.sh` now includes the R01 lifecycle harness alongside
boundary/API checks. Keep the macOS 14 deployment target unless owner requirements change;
that target is not evidence of physical Sonoma compatibility.
`xcodebuild -showsdks` is an additional inventory command when full Xcode is installed;
the old host selects Command Line Tools, and `xcodebuild` refuses that active selection.

```sh
bash native/build.sh
.build/voice-runtime/bin/python3 -B -m unittest discover -v -s tests -p 'test_*.py'
PYTHONPATH="$PWD/.build/test-deps" .build/voice-runtime/bin/python3 -B -m pytest tests/hub_regression -q
.build/voice-runtime/bin/python3 -B qualification/evidence/handoff-2026-09-20/reproduce_findings.py -v
```

The last command is a backlog reproducer: see the audit for expected failures.
Use those cases as regression checks when fixing the underlying defects. Root tests
already run the adopted Voice product suite in an isolated subprocess; do not infer
model qualification from fixture passes. Record newly skipped prerequisites explicitly.

### Build and inspect a new local app

From a clean checkout with verified runtimes and a newly compiled native shell:

```sh
.build/voice-runtime/bin/python3 -B packaging/stage_voice.py \
  --runtime .build/voice-runtime --native-shell .build/StudioHub \
  --image-runtime .build/st03-image-runtime-worker --output 'dist/Studio Hub.app'
.build/voice-runtime/bin/python3 -B packaging/release_kit.py \
  --app 'dist/Studio Hub.app' --library "$HOME/StudioHubModels" \
  --output .build/new-computer-release-manifest.json
```

The output app must not already exist. Check `release-manifest.json` for exact source
commit and development status, retain the adjacent tree hash, and require the kit's
`problems` list to be empty. This is local packaging, not deployment or a signed release.

Audit finding H-01 is fixed by reviewed PR #4: `qualification/verify-launch.sh` now
fails closed on identity, readiness, health and malformed-response errors. Run it with
explicit `APP`, `LIB` and an isolated port:

```sh
APP="$PWD/dist/Studio Hub.app" LIB="$HOME/StudioHubModels" PORT=0 \
  sh qualification/verify-launch.sh
```

For UI changes, the existing offscreen renderer exercises the real views:

```sh
bash native/render-page.sh all .build/new-computer-renders 1920x1053 1100x700 980x620
```

Inspect the images and interactive keyboard/export behavior. Static checks cannot
establish these. Real model tests, login/reboot, second physical Mac/fleet behavior,
and GenStudio integration must get receipts naming the exact new app and hardware.

## Authority and next-agent prompt

[MEASURED] The owner asked to identify bugs/fixes, write this handoff and ensure GitHub
is up to date for a new computer. Earlier accepted project implementation authority
is recorded in `AGENTS.md` and the board. Local reversible source/build/test work is
authorized. The standing repository rule permits reviewed, locally verified commits,
pushes, PRs and merge into the intended base; it does not permit deployment, publishing,
tags, paid calls, notarization submissions or real fleet enrollment. GitHub-hosted
compute is prohibited. At audit time Actions was enabled but had zero workflows;
no workflow files were present in the pushed branches. Recheck before future pushes.

Paste this on the new computer:

> Take over Studio Hub from this cloned repository. Read AGENTS.md, the top of
> docs/tasks/BOARD.md, docs/NEW-COMPUTER-HANDOFF.md, qualification/bug-audit-2026-09-20.md,
> docs/standalone-plan.md and docs/implementation-plan.md. Continue the existing
> .planning/2026-09-10-standalone-app-feasibility record. Establish the new Mac's
> hardware/toolchain and verify the copied/rebuilt inputs first. Do not equate a
> clone with a working runtime. Use the audit's prioritized fixes and runnable
> counterexamples; preserve the separate Music draft. Delegate bounded scanning and
> mechanical work to gpt-5.6-luna; retain design and independent verification as
> coordinator. Label measured, reported and assumed claims. Keep evidence and
> handoff state in Git. Do not touch old Pinokio machines, copy live credentials,
> run GitHub Actions, deploy, notarize or enroll a fleet node.

[MEASURED] The new computer's hardware, memory and macOS version are not stated in
the input as of this handoff. Its actual transfer/rebuild and app acceptance remain
to be performed there; this document does not claim that migration has happened.
