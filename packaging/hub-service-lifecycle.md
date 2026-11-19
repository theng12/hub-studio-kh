# Hub login-item service lifecycle (0.6.251)
One supported stop/start pair for one launchd label. It touches only `--label`
(default `kh.studiohub.standalone`) and deletes nothing.

## Installer contract: stop → swap → start → verify
```sh
LIFE=packaging/hub-service-lifecycle.sh
LABEL=kh.studiohub.standalone
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
HEALTH=http://127.0.0.1:PORT/api/health        # the app's own loopback health URL
"$LIFE" stop   --label "$LABEL" --plist "$PLIST" --health "$HEALTH"
# only now swap the bundle on disk
"$LIFE" start  --label "$LABEL" --plist "$PLIST" --health "$HEALTH"
"$LIFE" verify --label "$LABEL" --plist "$PLIST" --health "$HEALTH"
```
`stop` boots out `gui/$(id -u)/<label>` (exit 3 = already absent), polls `launchctl print` until
the job is gone, then polls health until it stops answering. `start` lints the plist, runs
`launchctl enable` on the exact target (clears a disabled override left by the old code
requirement, issue 20261119-60), bootstraps, then requires a running pid and a health reply whose
`pid` equals that launchctl pid. `verify` prints comparable state / pid / last exit code / health
pid lines for a receipt. Every wait has a 30-second ceiling and prints the state each poll.

## What a failure means
- `bootout failed`, or job still loaded after the bound: the old binary is still supervised; do not
  swap. Quit the app from its menu and retry `stop`. Health still answering names its live pid.
- `plist missing` / `plist failed lint`: restore the original plist and retry `start`.
- `bootstrap failed (exit 5)` / `Bootstrap failed: 5: Input/output error`: no service is loaded.
  **Retry `start` with the original plist before walking away** — never leave the Mac with no
  login item and no message. A service that already exists means run `stop` first.
- `spawn failed` / `needs LWCR update`: stale cached code requirement; reload in order
  (bootout, enable, bootstrap). No running pid / health pid mismatch: read `launchctl print
  gui/$(id -u)/<label>`, fix the plist or app, then retry `start`. Every failure names the state.

## Proof
`packaging/hub-service-lifecycle-selftest.sh` uses a temp label, plists and a sleeping job with a
loopback pid health reply; it never references the real Hub label. Its log,
`.build/hub-service-lifecycle-selftest.log`, records the exact `state = running`,
`state = SIGTERMed`, `Could not find service`, `pid` and `last exit code` strings, the invalid-plist
bootstrap failure and the recovery. Its trap boots the temp label out and removes the temp
directory on success, failure, INT and TERM.
