# Ordered handoff completion — wave 1

[MEASURED] R01 and R02 are independently reviewed and locally verified. Their
integration is tracked by [PR #12](https://github.com/theng12/Studio-Hub-KH/pull/12),
whose merge state/hash is authoritative. No later roadmap task is qualified here.

## Exact source and artifact

- Plan base: `0ec3b3b09c9642b670ec0a092f4223ea4ffe0e24`.
- R01 native repair: `0e116711535618250cbbdd8cb48b4c190bf352fd`.
- R02 / clean tested product source: `7ec9f89dd4a225ba2431747f746d56dc786e4ef4`.
- App: `/build/.codex/worktrees/handoff-completion/studio-hub-kh/.build/StudioHubWave1.app`.
- Version 0.6.38, `development_build=false`; this is a local candidate, not a release.
- App tree SHA256: `07c4d895b795513c9c5f8d1d073d34c90a31a40e5bd950f06f797bfa85d8cb25`.
- Native binary SHA256, equal before/after staging:
  `39f03515ead0747b793c7d8fb63bf058221ec70506db16c22fc12c74b7c9829d`.
- Reference module SHA256, equal in source and app:
  `f04cfea87aadde8d58efafbf3f9f99ec410f42a1b9f1a1ec336248a745c4947a`.

This evidence-only child does not replace the product source identity with its
own later Git hash. Reproduction of HEAD-bound release assertions must check out
the exact tested product commit or restage a candidate at the desired source.

## Verification

| Check | Measured result |
| --- | --- |
| Root full regression, exact candidate selected | 632 tests, OK, 7 skipped, 415.052 s |
| Nested Voice product suite | 722 passed, 19 skipped, 20.77 s |
| Nested isolated Hub regressions | 85 passed, 1.64 s |
| Focused references/purge/standalone | 19 passed; includes 28 child HTTP cases |
| Native boundary/API + optimized build | passed, worker and independent root rerun |
| Real-controller lifecycle harness | passed; unhealthy, healthy, late response |
| Release notes / bundled phonemizer | 21 passed, 1.57 s |
| Original five handoff probes | 5 passed, 3.602 s |
| Packaged four-worker model-free probe | passed; 10.01 s readiness |

Native RED→GREEN commands, implementation reasoning and limits are in
[R01 report](../.planning/2026-09-10-standalone-app-feasibility/r01-report.md).
Reference corruption/input RED→GREEN and independent review are in
[R02 report](../.planning/2026-09-10-standalone-app-feasibility/r02-report.md).
Independent `music_recovery_read` review found no actionable findings in either
slice; root independently inspected the whole source/test diff and reran native
proofs. No deferred minor code findings.

The preliminary full run selected the prior `c9eadca` artifact and failed exactly
its source-commit assertion. A clean restage at `7ec9f89` and the full rerun above
resolved that failure without weakening assertions.

Root skips: one unmounted SSD-kit check, one missing local model-catalog check,
five real Whisper checks without configured model inputs. Upstream deprecation,
syntax and fixture socket ResourceWarnings remain visible; they are not test
failures or proof of real-model coverage.

## Commands

From the preserved handoff-completion worktree, at tested product commit:

```sh
bash native/build.sh
.build/native-lifecycle-tests-r01-final

.build/voice-runtime/bin/python3 -B packaging/stage_voice.py \
  --runtime .build/voice-runtime --native-shell .build/StudioHub \
  --image-runtime .build/st03-image-runtime-worker --music-runtime .build/music-runtime \
  --music-engine /build/Developer/studio-hub-kh/.build/music-engine-source \
  --output .build/StudioHubWave1.app

STUDIOHUB_TEST_APP="$PWD/.build/StudioHubWave1.app" \
STUDIOHUB_TEST_MUSIC_OVERLAY="$PWD/.build/music-runtime" \
STUDIOHUB_TEST_MUSIC_ENGINE=/build/Developer/studio-hub-kh/.build/music-engine-source \
STUDIOHUB_TEST_VOICE_PYTHON="$PWD/.build/voice-runtime/bin/python3" \
STUDIOHUB_TEST_RUNTIME="$PWD/.build/voice-runtime/bin/python3" \
PYTHONPATH="$PWD/.build/test-deps" \
.build/voice-runtime/bin/python3 -B -m unittest discover -v -s tests -p 'test_*.py'

STUDIOHUB_TEST_APP="$PWD/.build/StudioHubWave1.app" PYTHONPATH="$PWD/.build/test-deps" \
.build/voice-runtime/bin/python3 -B -m pytest -q \
  tests/test_release_notes.py tests/test_espeak_library_available.py

.build/voice-runtime/bin/python3 -B qualification/evidence/music-runtime-2026-09-21/probe.py \
  --app .build/StudioHubWave1.app --output .build/wave1-packaged.json
.build/voice-runtime/bin/python3 -B qualification/evidence/handoff-2026-09-20/reproduce_findings.py -v
```

The stage command refuses an existing artifact: choose a new output name for a
new run, never overwrite the verified app. The lifecycle harness compile command
is in R01's report; portable default-build wiring remains R05. Local raw logs are
`.build/root-native-final.log`, `.build/wave1-full-regression.log`,
`.build/wave1-release.log`, `.build/wave1-original-probes.log` and
`.build/wave1-packaged.log`.

## Packaged result and boundaries

The [saved receipt](evidence/handoff-completion-2026-09-21/packaged.json) records
Image/Music/Voice/Subtitle worker presence, authenticated health/catalog, refusal
of unauthenticated Music access, removal of owned processes/readiness, and an
unchanged app tree. Source comparison additionally checked the repaired reference
module and built native binary. The probe starts the packaged service, not a GUI
generation workflow; native controller behavior is covered separately by fixtures.

No model inference, physical fleet enrollment, GUI Save-panel acceptance, original
M1/Sonoma, login/reboot, deployment, installed-app replacement, release/tag or
notarization. Existing Pinokio and the protected Downloads model library remain
untouched. All compute ran locally; GitHub reported zero workflows before pushes.

Next: R03's verified Voice receipt and WAV-payload defects. Preserve idempotent
reposts and intentional generated-but-unverified compatibility; see
[R03 preflight](../.planning/2026-09-10-standalone-app-feasibility/r03-preflight.md).
R04–R10 remain in the [canonical ordered plan](../.planning/2026-09-10-standalone-app-feasibility/task_plan.md).
