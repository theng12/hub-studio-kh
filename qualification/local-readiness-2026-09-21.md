# Local readiness and GatewayKH continuation — 2026-09-21

## Outcome and exact identity

[MEASURED] Reviewed product changes: `fb36c89dddf3592209c51e86e2699f07a6b30fd5`.
Final clean tested candidate, including two corrected legacy proof fixtures:
`936b3e5aba42c0867559031e95c47937d9b091d4`. Integration is tracked by
[PR #13](https://github.com/theng12/Studio-Hub-KH/pull/13); its merge state and
merge commit are authoritative. Later evidence-only commits do not change product code.

- R03: malformed Voice acceptance/status/recovery is held uncertain; worker-local
  replay cannot justify cross-worker regeneration. Invalid JSON, changed identities,
  missing artifact references, cancellation races, fractional byte counts and
  truncated PCM WAVs have permanent regressions. Valid completed-but-unverified
  output remains terminal with `artifact_metadata_error`.
- R05 **partial**: explicit shared SDK selection, no silent fallback, and native
  lifecycle gate before optimized compilation. Other bootstrap work remains open.
- GatewayKH: current native connection copy and scoped-token guidance; counterpart
  source inventory at `736d7b05625fcc46ccdafdc7206319c9214f9b27`. No counterpart edits
  or live connection. Existing protocol identifiers remain unchanged.
- R04: all four node-owned admission bypasses reproduced again on the final
  candidate; **not repaired**. This is the next priority after integration.

Mac14,13 / Apple Silicon / 32 GiB / macOS27.0; native SDK26.5 targets macOS14.0.
This is not physical Sonoma/original-M1 qualification. Installed apps, existing
Pinokio machines and the protected Downloads model library were untouched.

## Verification

| Check | Direct result |
| --- | --- |
| Final root `unittest discover -v -s tests -p 'test_*.py'` | 641 tests, OK, 7 skips; 428.429s |
| Nested complete Voice suite | 722 passed, 19 skipped, 5 warnings; 20.83s |
| Fresh isolated Hub regressions | 108 passed, 2 upstream warnings |
| Native SDK26.5 build | Boundary, API, lifecycle and optimized arm64 build passed |
| Native build-script behavior | 8 passed; fake tools isolated from checkout outputs |
| Release/bundled phonemizer, final app selected | 21 tests, OK; 1.206s |
| Original five handoff probes | 5 tests, OK |
| Corrected duplicate Voice proof modules | Root rerun: 8 tests, OK; 15.401s |
| GatewayKH existing offline adapter checkpoints | 3 tests, OK; mocked HTTP only |
| Final package startup/auth/shutdown | Image, Voice, Subtitle and Music workers; 401 without auth; owned cleanup; bundle unchanged |

The final root command ran with these explicit inputs in the preserved worktree:

```sh
STUDIOHUB_TEST_APP="$PWD/.build/StudioHubLocalReadinessV2.app" \
STUDIOHUB_TEST_MUSIC_OVERLAY="$PWD/.build/music-runtime" \
STUDIOHUB_TEST_MUSIC_ENGINE=/build/Developer/studio-hub-kh/.build/music-engine-source \
STUDIOHUB_TEST_VOICE_PYTHON="$PWD/.build/voice-runtime/bin/python3" \
STUDIOHUB_TEST_RUNTIME="$PWD/.build/voice-runtime/bin/python3" \
PYTHONPATH="$PWD/.build/test-deps" \
.build/voice-runtime/bin/python3 -B -m unittest discover -v -s tests -p 'test_*.py'
```

Logs: `.build/local-readiness-full-regression-v2.log`,
`.build/voice-product-suite.log`, `.build/local-readiness-native.log`,
`.build/local-readiness-release-v2.log`, `.build/local-readiness-packaged-v2.log`.
Restage at a newer HEAD before reusing the source-identity gate: this artifact
belongs to `936b3e5`, not a later evidence/merge commit.

## Artifact and review

Preserved app: `.build/StudioHubLocalReadinessV2.app` in
`/build/.codex/worktrees/handoff-completion/studio-hub-kh`.
Version0.6.38, `development_build:false`, `qualification:unqualified-local-proof`.
Tree SHA256: `06e881146e69e88097df71291569ffa873acf541381dc3fb8010df1ec34024e7`.
Native SHA256: `2e7fa67d3d68208af2b859123345d9c40a76c7719e3677eaa7d5f09a8b936848`.
Broker/parser source bytes match the bundle; exact hashes and smoke results are in
[the sanitized receipt](evidence/local-readiness-2026-09-21/packaged-smoke.json).
The existing [model-free probe](evidence/music-runtime-2026-09-21/probe.py) was used
with disposable state/model directories. It loaded no model, enrolled no real node,
and changed no installed app. Exit -15 is the requested service SIGTERM, followed
by verified removal of all owned processes/readiness records.

Independent Sol/high integrity review found four blockers, all reproduced and
repaired before its clean final verdict. Reviewed eight-file diff SHA256:
`a2f4022027a57251fb30164901db6b8a8931d04445fd0e5ed53bc424b13cf915`.
Terra independently reviewed the native build slice; root reran its checks and
reviewed GatewayKH copy plus the two later fixture-only corrections. No test
assertions or production validation were weakened. Details:
[R03](../.planning/2026-09-10-standalone-app-feasibility/r03-report.md),
[proof fixtures](../.planning/2026-09-10-standalone-app-feasibility/r03-proof-fixtures-report.md),
[native build](../.planning/2026-09-10-standalone-app-feasibility/r05-native-build-report.md).

## Earlier failure and remaining limits

The first full run at `fb36c89` failed three tests (641 total, 7 skips, 449.455s):
two legacy Voice fixtures omitted real receipt fields; both were corrected.
One shared-voice peer reachability miss did not recur in its isolated rerun
(1 passed, 13.68s), complete class (6 passed, 61.42s), or final full run. Its cause
is unproven and it is **not declared fixed**. Dependency deprecations and fixture
process/socket ResourceWarnings remain visible. Named warned subprocesses had
exited when checked; no installed service was stopped to make tests pass.

Impeccable informed concise GatewayKH/token-scope wording. Offline disconnected
Connections renders at 1040×680 and 1920×1053 were inspected; populated token/identity
states, accessibility interaction and actual GUI Save/ACK remain unqualified.
Graphify CLI was unavailable and its old graph was unsuitable; callers were
inspected directly, with no fabricated graph coverage or paid extraction.

Next: [R04 negative proof](../.planning/2026-09-10-standalone-app-feasibility/r04-preflight.md)
and node-owned pause/disable/storage/busy enforcement, preserving access to already
accepted jobs. The [GatewayKH inventory](../.planning/2026-09-10-standalone-app-feasibility/gatewaykh-contract-inventory.md)
records missing artifact ACK, upstream cancellation and Music, plus outstanding
reference/recovery semantics and end-to-end qualification. Native Music, real model
inference, physical fleet/clean-Mac/Sonoma/login/reboot and distribution gates remain
open. No hosted compute, paid calls, deployment, signing/notarization, tags or releases.
