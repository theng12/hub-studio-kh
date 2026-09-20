# R01 native lifecycle repair report

Base: `0ec3b3b09c9642b670ec0a092f4223ea4ffe0e24`

Status: implemented and locally verified; uncommitted for coordinator review/integration.
Graphify CLI was unavailable and the saved Voice graph does not cover this native
lifecycle, so callers were traced directly in the current source.

## Files

- `native/StudioHub.swift`
- `native/StudioAPI.swift`
- `tests/native/api_main.swift`
- `tests/native/lifecycle_main.swift` (coordinator-approved focused harness)
- `.planning/2026-09-10-standalone-app-feasibility/r01-report.md`

`native/StartupRecovery.swift` and `tests/native/main.swift` were inspected but did
not need changes. No build script, installed app, preference, login item, production
service, model library, enrollment, deployment or Git state was changed.

## Root causes and repair

- N-01: readiness decoding started `healthySince`, so an alive but authenticated-health-
  failing process could reset the restart budget after 60 seconds. Readiness no longer
  starts the interval. The first valid authenticated `api/health` success starts it,
  every health failure clears it, and exit evaluates the injected/current clock.
- N-03: the health success branch did not re-check cancellation, current `Process`
  identity or process liveness after its await. Every lifecycle await now has the
  ownership guard, and a termination callback from a replaced process is ignored.
- N-02: startup identity/version and rejoin ran once, outside the existing two-second
  local refresh. A failed identity read now leaves a pending membership that the
  existing poll retries. There is at most one automatic attempt for the current
  startup membership; the remote join runs
  independently so its 30-second timeout cannot stall local catalog/job refresh.
  Success clears the attempt. Manual join, disconnect/token replacement, explicit
  leave, a changed connection, missing token, or a non-adopted identity invalidates it.

No generic scheduler or duplicate lifecycle implementation was added.

## RED evidence

N-02 transient identity, before product edits:

```sh
SDK_PATH=$(xcrun --sdk macosx26.5 --show-sdk-path)
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 \
  native/ShellCore.swift native/StartupRecovery.swift native/ReleaseNotes.swift \
  native/StudioAPI.swift tests/native/api_main.swift \
  -o .build/native-api-tests-r01-red
.build/native-api-tests-r01-red
```

Exit 133: `Fatal error: the lifecycle refresh must recover one pending re-join: []`.

N-02 held remote join, after adding the non-blocking regression and before making the
remote attempt independent:

```sh
SDK_PATH=$(xcrun --sdk macosx26.5 --show-sdk-path)
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 \
  native/ShellCore.swift native/StartupRecovery.swift native/ReleaseNotes.swift \
  native/StudioAPI.swift tests/native/api_main.swift \
  -o .build/native-api-tests-r01-red-held
.build/native-api-tests-r01-red-held
```

Exit 133: `Fatal error: a held controller retry stalled the local lifecycle refresh`.

N-01 behavior was re-run with an untracked `.build/r01-red-n01/StudioHub.swift`
copied from the final source and mechanically restored to the original two faulty
lines (start health at readiness; do not clear it on failed health). The test waits
for the controller to consume a real authenticated 503 on each run before advancing
the controlled clock and terminating the owned fixture process:

```sh
mkdir -p .build/r01-red-n01
cp native/StudioHub.swift .build/r01-red-n01/StudioHub.swift
perl -0pi -e 's/self\.starting = false\n                        self\.readyURL/self.starting = false\n                        self.healthySince = self.now()\n                        self.readyURL/' .build/r01-red-n01/StudioHub.swift
perl -0pi -e 's/\n                                self\.healthySince = nil\n                                self\.status = "Local services not responding"/\n                                self.status = "Local services not responding"/' .build/r01-red-n01/StudioHub.swift
SDK_PATH=$(xcrun --sdk macosx26.5 --show-sdk-path)
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 \
  -D LIFECYCLE_TESTING -parse-as-library \
  native/ReleaseNotes.swift native/ShellCore.swift native/StartupRecovery.swift \
  native/StudioAPI.swift native/StudioViews.swift \
  .build/r01-red-n01/StudioHub.swift tests/native/lifecycle_main.swift \
  -o .build/native-lifecycle-tests-r01-red-n01
.build/native-lifecycle-tests-r01-red-n01 unhealthy
```

Exit 133: `Fatal error: mere readiness reset the crash budget: Local services not responding`.

N-03 behavior was re-run with an untracked `.build/r01-red-n03/StudioHub.swift`
copied from the final source and mechanically removing only the post-await ownership
guard. The held health continuation deliberately completes after the owned process
has exited and terminal recovery state is visible:

```sh
mkdir -p .build/r01-red-n03
cp native/StudioHub.swift .build/r01-red-n03/StudioHub.swift
perl -0pi -e 's/                                guard !Task\.isCancelled, !self\.stopping, process\.isRunning,\n                                      self\.worker === process else \{ return \}\n                                if self\.healthySince/                                if self.healthySince/' .build/r01-red-n03/StudioHub.swift
SDK_PATH=$(xcrun --sdk macosx26.5 --show-sdk-path)
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 \
  -D LIFECYCLE_TESTING -parse-as-library \
  native/ReleaseNotes.swift native/ShellCore.swift native/StartupRecovery.swift \
  native/StudioAPI.swift native/StudioViews.swift \
  .build/r01-red-n03/StudioHub.swift tests/native/lifecycle_main.swift \
  -o .build/native-lifecycle-tests-r01-red-n03
.build/native-lifecycle-tests-r01-red-n03 late
```

Exit 133: `Fatal error: late health response overwrote terminal state: Local services ready`.

All RED variants were confined to ignored `.build`; tracked product source was not
reverted or replaced.

## GREEN evidence

Fresh final focused lifecycle harness plus full native boundary/API/build gate:

```sh
pkill -f '/studiohub-lifecycle-.*/StudioHubService' 2>/dev/null || true
SDK_PATH=$(xcrun --sdk macosx26.5 --show-sdk-path)
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 \
  -D LIFECYCLE_TESTING -parse-as-library \
  native/ReleaseNotes.swift native/ShellCore.swift native/StartupRecovery.swift \
  native/StudioAPI.swift native/StudioViews.swift native/StudioHub.swift \
  tests/native/lifecycle_main.swift -o .build/native-lifecycle-tests-r01-final
.build/native-lifecycle-tests-r01-final
bash native/build.sh
git diff --check
if pgrep -f '/studiohub-lifecycle-.*/StudioHubService' >/dev/null; then
  echo 'fixture process leak'; exit 1
fi
```

Exit 0. Exact output:

```text
Native lifecycle checks passed
Native boundary checks passed
Native API contract checks passed
```

`native/build.sh` also compiled the full optimized `.build/StudioHub` executable.
No fixture service remained running.

The lifecycle harness proves, with no real-time 60-second wait:

- two ready-but-authenticated-unhealthy runs exhaust a one-restart budget;
- a continuous valid authenticated-health interval of 61 seconds resets it;
- a late old-process health success cannot replace terminal failure state.

The API fixture proves transient identity recovery, one successful join only,
minted/non-adopted refusal, non-blocking local refresh during a held controller
request, and invalidation by manual join, disconnect/token replacement and leave.

## Public behavior and remaining limits

There is no new route, setting or UI. Observable behavior changes are limited to
more accurate crash recovery and automatic recovery of a temporarily missed fleet
rejoin. Controller retry cadence is the existing two-second local poll, with one
automatic request task in flight at a time. Superseding an already-sent request
cannot retract a remote side effect; stale local completion is rejected.

The controller harness uses a temporary executable, loopback fixture server,
controlled clock and injected held health request. It does not qualify a packaged
app, real controller hardware, physical enrollment, OS login/reboot or external
network behavior. The transient metadata test injects the identity half of the
identity/version pair; both requests share the same `refreshConnection` failure and
retry path, but a separate version-only failure was not added. Final packaged
staging, Python gate and integration remain coordinator-owned.

## Coordinator review

Root inspected the complete source/test diff and real callers; independent
`music_recovery_read` review found no actionable native or R02 findings. R02 was
also independently tested. Slow-controller refresh blocking and stale join UI
ownership were considered during review and are covered by the implementation.
No new visual surface was introduced; Impeccable's offline/concurrency hardening
guidance informed state review. Its context launcher was not executable, so root
read existing PRODUCT.md/DESIGN.md directly; no design metadata was modified.

The newly added lifecycle runner is executed explicitly by the coordinator in
this release gate. Wiring it into portable `native/build.sh` belongs to R05;
future gates must not silently omit the command documented above.
