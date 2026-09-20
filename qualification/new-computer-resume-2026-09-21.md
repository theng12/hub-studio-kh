# New-computer continuation — 2026-09-21

## Source and scope

[MEASURED] Resumed the preserved `codex/music-package-runtime` worktree from
`9f25c0310dcbe1223f323da94e845d174e043960`. GitHub and the clean integration checkout
matched that commit; it contains the original handoff and reviewed H-01 through
H-05 fixes plus the bounded Music package contract. Existing runtime work was
preserved. The old Music draft was not merged wholesale.

This segment packages the pinned Music engine source and dependency overlay,
supervises its private worker, classifies its Hub routes, and extends durable job
recovery to Music. It also corrects the local verification gate's artifact and
phonemizer assumptions. Native Music controls and real Music inference remain open.

## Environment and inputs

[MEASURED] `uname -m`, `sw_vers`, `sysctl -n hw.model hw.memsize`, `df -h .`,
`xcode-select -p` and `xcrun --sdk macosx26.5 --show-sdk-path` established arm64,
Mac14,13, 32 GiB RAM, macOS 27.0 (26A428), Command Line Tools, and SDK 26.5.
Available disk was 286 GiB at this resume's intake. The native build completed,
including boundary and API fixtures, with its existing macOS 14 deployment target.
Physical Sonoma/M1 qualification is still absent.

[MEASURED] The main checkout's rebuilt `.build/voice-runtime` passed
`runtime_packaging.py verify`. The installed app's immutable Image runtime had
the exact tracked Image lock (`cmp`, exit 0) and passed the same verifier. Only
that runtime was copied to `.build/st03-image-runtime-worker`, where verification
passed again. The runtime was not adopted based on the installed app's version.
Worktree build paths reference these project-owned runtimes and separate test
dependencies. Both Image wrapper/private-server tests then passed.

No model packages, credentials, live job databases, preferences, or service
registration were copied. The protected Downloads library was not scanned or
modified. The existing installed app and its running services remain untouched.

## Review repairs

[REPORTED, then covered by local regression checks] Independent review of the
preserved runtime work required moving ACE-Step progress state outside immutable
source, terminating orphaned engine children, retaining Music acceptance before
polling, and providing Music reconciliation. Final review of that reconciliation
reproduced two further defects:

- The worker normalizes `duration` to `duration_seconds` and trims prompts.
  Comparing the terminal receipt with raw input stranded valid completed jobs.
  Music now stores the canonical acceptance parameters durably and compares that
  exact snapshot with the original worker/job/model/runtime/machine identity.
- A failed terminal ledger save could leave live state `done` while disk remained
  `uncertain`, releasing the machine fence and refusing a later reconciliation.
  Publication now retains uncertainty on save failure, restores finalization
  flags, and sends callbacks only after durable publication. Retried adoption
  keeps the same asset ID and clears stale uncertainty errors after success.

Legacy uncertain Music receipts lacking canonical acceptance parameters remain
fenced for manual reconciliation; they are not silently retried or inferred valid.

[REPORTED] Independent final review of the frozen diff against `9f25c03` found no
Critical, Important or Minor findings and approved this bounded segment for merge.
The reviewer independently passed 85 Hub cases, 46 Music/package/baseline tests
(four expected skips), 37 Hub/auth/peer tests, and three authenticated HTTP
fault-injection reproductions. The reproductions covered normalized parameters,
done/error/cancelled terminal-save failures, retained fences, exactly one generation
POST, callback ordering and asset-ID reuse. Coordinator verification follows below.

## Verification gate corrections

[MEASURED] Initial resumed root discovery ran 626 tests in 376.992 seconds:
three failures, one error, 11 skips. The failures were the mandatory `espeak-ng`
command assumption and two checks against an installed 0.5.4 app while source is
0.6.38. The error was a missing worktree-local runtime path, subsequently restored.
The nested Voice suite passed 722 with 19 skips.

[MEASURED] The pinned Kokoro pipeline uses Misaki's library-backed EspeakFallback;
the real phonemizer returned `həloʊ` without an espeak executable. The corrected
test exercises this production fallback in a fresh interpreter with a restricted
PATH, temporary state/cache and offline flags. It preserves library existence and
operator-override checks. No new runtime dependency was added.

[MEASURED] Staged release checks now select `STUDIOHUB_TEST_APP` explicitly or the
repository's own `dist/Studio Hub.app`, fail for an explicit missing path, and check
the manifest against the current source commit. They no longer select arbitrary
installed apps. Version, display-name and release-note assertions remain enforced.

[MEASURED] `unittest.defaultTestLoader.discover` returned zero HubRegressionTests
before adding `tests/hub_regression/__init__.py` and exactly one afterward. This
restores the intended isolated Hub bridge to ordinary root discovery.

[MEASURED] Before the final recovery repairs, 111 affected packaging/model/Hub
tests plus 122 subtests passed, the five original audit probes passed, 19 storage
tests passed with one skip, and native build passed. The assembled service proof
checked authentication, Music runtime identity, empty-model readiness, owned-process
shutdown and unchanged bundle bytes. Final gate results and artifact identity are
recorded below; these checks are not release qualification.

## Final local gate

[MEASURED] On the reviewed product diff against `9f25c03`, root discovery exited 0:
631 tests in 442.126 seconds, OK with seven skips. Nested logs show 85 Hub passes
and 722 Voice passes with 19 skips (20.76 seconds). Root skips require an SSD kit
or real model fixtures; they are not model qualification. The gate emitted
fixture socket ResourceWarnings and one killed-process reaping warning; that
process was verified absent afterward. These do not replace the independent
packaged shutdown proof. No test assertions failed or were weakened to pass.

Reproduction from this worktree (its `.build` runtime/test-dependency paths point
to the verified project-owned inputs):

```sh
STUDIOHUB_TEST_APP="$PWD/.build/StudioHubCandidate.app" \
STUDIOHUB_TEST_MUSIC_OVERLAY="$PWD/.build/music-runtime" \
STUDIOHUB_TEST_MUSIC_ENGINE=/build/Developer/studio-hub-kh/.build/music-engine-source \
STUDIOHUB_TEST_VOICE_PYTHON="$PWD/.build/voice-runtime/bin/python3" \
STUDIOHUB_TEST_RUNTIME="$PWD/.build/voice-runtime/bin/python3" \
PYTHONPATH="$PWD/.build/test-deps" \
.build/voice-runtime/bin/python3 -B -m unittest discover -v -s tests -p 'test_*.py'
```

[MEASURED] The final precommit candidate contains native Image/Voice/Subtitle/Music
and has tree SHA-256
`faea409d915ce467866d6b96d7b2fcdd378dfd6b0c5118490cd59e39a6a8c92d`.
It honestly records base `9f25c03` and `development_build: true`; changed packaged
module bytes matched current source. The authenticated smoke passed in 9.68 seconds,
Music identity matched all three boundaries, unauthenticated access returned 401,
model checkpoints remained missing and the engine unloaded. Owned processes and
readiness descriptors disappeared; bundle bytes were unchanged afterward.

The parameterized probe and sanitized JSON receipt are under
`qualification/evidence/music-runtime-2026-09-21/`. A clean-commit artifact check
will supersede the precommit receipt there before integration. The original audit
probes passed again (five tests, 3.573 seconds). Native boundary/API/build checks
passed, and `git diff --check` passed. GitHub still reports zero Actions workflows.

## Remaining audit assessment

[MEASURED, static] N-01, N-02 and N-03 remain present in current source:
`StudioHub.swift` starts the healthy interval at readiness and lacks a post-await
current-process guard; `StudioAPI.swift` attempts startup identity/rejoin once.
Delayed-response, unhealthy-uptime and transient-identity fixtures remain required
before fixing or closing these native findings.

[MEASURED] A temporary authenticated reference-upload fixture returned HTTP 201
for each of string `NaN`, string `Infinity`, negative and reversed segment timings
(C-01). No model-specific failure was inferred from that boundary acceptance.
Restaging the same fixture after changing only its metadata `expires_at` to
`invalid-deadline` returned HTTP 500 and preserved the metadata (C-02). Both remain
open for bounded input/metadata repair; no real audio or library was used.

[MEASURED, static] C-03 still warrants malformed Voice acceptance/status fault
injection: acceptance shape/state validation remains stricter for Image/Music.
C-04 remains an unproven admission concern: fleet generation uses the intended
worker proxy, so a real paused/storage/occupied-machine fixture is required before
changing that routing contract. Neither is promoted to an end-to-end failure here.

M-01 and M-02 are locally unblocked by the available SDK and verified Image runtime.
Fresh offline Image cache reconstruction remains unproven. M-03 is addressed by
separate test dependencies/worktree paths. M-04 remains an installed-app deployment
boundary; local candidate packaging is not replacement. M-05's older fleet harness
defaults remain historical and need explicit paths before use. M-06's invalid Music
catalog contract was resolved in PR #10; inference/UI qualification remains open.

[MEASURED] Graphify's executable and saved interpreter were unavailable. The existing
747-node graph had no Music/broker/reconciliation matches; current callers were
inspected directly. No new graph was committed, and no graph coverage is claimed.

## Next work

Finish independent review, verify the exact final candidate, and integrate through
the authorized PR workflow. Then reproduce/repair native lifecycle findings and
reference validation candidates before native Music workflow and model qualification.
Physical second-Mac/fleet, login/reboot, M1/Sonoma and GenStudio acceptance remain
explicit later evidence. No hosted compute, paid calls, deployment, release, tag,
notarization or real fleet enrollment was performed.
