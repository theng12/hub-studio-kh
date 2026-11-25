#!/bin/bash
# Proves a launch opens nothing on the owner's screen: runs an app bundle's own binary hidden,
# with its own empty data directory, samples the window server for a few seconds, and fails if the
# process ever owns a visible window. Read-only against the screen; it never activates the app.
#
#   STUDIOHUB_BACKGROUND=1 tests/native/hidden-launch.sh ".build/Hub Studio KH.app" [seconds]
#
# The caller's environment is passed through, so each app names its own background/quiet flag. The
# app is terminated (SIGTERM) at the end - it is a throwaway instance with a temp data dir.
set -euo pipefail
ROOT_DIR=$(cd -- "$(dirname -- "$0")/../.." && pwd)

app="${1:?usage: hidden-launch.sh <app-bundle> [seconds]}"
seconds="${2:-6}"
exe="$app/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$app/Contents/Info.plist")"
[ -x "$exe" ] || { echo "Stopped: no executable at $exe" >&2; exit 1; }
# A proof that can pass on a fixture key is not a proof of the shipped app: refuse unless the
# bundle carries this app's own EdDSA public half.
key="$(/usr/libexec/PlistBuddy -c 'Print :SUPublicEDKey' "$app/Contents/Info.plist" 2>/dev/null || true)"
[ "${#key}" -eq 44 ] || { echo "Stopped: SUPublicEDKey is missing or not 44 base64 chars (got '${key}')" >&2; exit 1; }
real="$(cat "$HOME/.kh-sparkle/local.studiohub.standalone/public.key" 2>/dev/null || true)"
[ "$key" = "$real" ] || { echo "Stopped: SUPublicEDKey is not Hub's own public key" >&2; exit 1; }

probe="${TMPDIR:-/tmp}/kh-window-count"
xcrun swiftc -O "$ROOT_DIR/tests/native/window_count.swift" -o "$probe"
data=$(mktemp -d "${TMPDIR:-/tmp}/kh-hidden-launch.XXXXXX")
log="$data/app.log"

# `--data-dir` is the shell's own override (it is what keeps a copy off the owner's real data and
# off the login registration); `--background` keeps it windowless.
"$exe" --background --data-dir "$data" >"$log" 2>&1 &
pid=$!
sleep 1
alive() { kill -0 "$pid" 2>/dev/null; }
alive || { echo "FAILED: the app exited within a second; log:"; cat "$log"; exit 1; }

peak=0
elapsed=0
while [ "$elapsed" -lt "$seconds" ]; do
    alive || { echo "FAILED: the app exited after ${elapsed}s; log:"; cat "$log"; exit 1; }
    count=$("$probe" "$pid")
    [ "$count" -gt "$peak" ] && peak=$count
    sleep 0.5
    elapsed=$((elapsed + 1))
done

kill -TERM "$pid" 2>/dev/null || true
wait "$pid" 2>/dev/null || true

echo "app pid $pid ran ${elapsed}s hidden, data dir $data"
echo "windows ever on screen: $peak"
grep -i "kh-sparkle" "$log" || true
if [ "$peak" -ne 0 ]; then
    echo "FAILED: the app opened $peak window(s) on the owner's screen" >&2
    exit 1
fi
echo "PASS: no window opened"
