#!/bin/sh
# Studio Hub KH — does this copy actually start?
#
# Run this on the machine that has the app, after copying it. It starts the app's own
# bundled service with the app's own arguments, waits for it to become ready, asks it for
# its health, and stops it again. That is the one check a copied app must pass, and it is
# the check that was missing when the app shipped a launch flag the worker did not accept:
# the app said only "the engine exited (code 2)" and every automated test had driven the
# worker directly, so nothing exercised the app's own command line.
#
# It never touches your real state. It starts the service with a fresh temporary data
# directory AND a fresh, EMPTY temporary model library, both under $TMPDIR,
# so no model, library or preference of yours is read, moved or shelved. (Until 2026-10-02 it was
# given the real library next to a throwaway data directory; an app older than the library-
# ownership check then shelved/retired real weights into that throwaway directory's Trash Folder,
# which destroyed them. A library is therefore never passed to the service here.)
#
#   sh "/Volumes/KH-STUDIO-ATLAS/1 Install Apps/Hub Studio KH/verify-launch.sh"
#
# Override the app if yours differs:
#   APP="/Applications/Studio Hub 0.4.0.app" sh verify-launch.sh
# LIB= is accepted for old command lines and ignored.

# Defaults that work with no environment at all, which is what happens when this is
# double-clicked in Finder rather than run from a terminal.
KIT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ -z "${APP:-}" ]; then
  for _cand in "/Applications/Hub Studio KH.app" "/Applications/Studio Hub 0.4.0.app" \
               "$HOME/Applications/Hub Studio KH.app" "$HOME/Applications/Studio Hub 0.4.0.app" \
               "$KIT/Hub Studio KH.app" "$KIT/Studio Hub 0.4.0.app"; do
    [ -d "$_cand" ] && { APP="$_cand"; break; }
  done
  APP="${APP:-$KIT/Hub Studio KH.app}"
fi
# LIB (a real library) is deliberately not used: see the header.

SVC="$APP/Contents/MacOS/StudioHubService"
PY="$APP/Contents/Resources/runtimes/voice/bin/python3"

echo "app:     $APP"
echo "library: (an empty temporary one; your real model library is not used)"
echo

fail() { echo "RESULT: FAIL — $1"; exit 1; }

[ -d "$APP" ] || fail "no app at $APP"
[ -x "$SVC" ] || fail "the bundled service is missing or not executable (replace this app with a complete copy)"
[ -x "$PY" ]  || fail "the bundled Python runtime is missing or not executable (replace this app with a complete copy)"
MANIFEST="$APP/Contents/Resources/release-manifest.json"
identity=$("$PY" - "$MANIFEST" <<'PY'
import json
import sys

with open(sys.argv[1]) as stream:
    manifest = json.load(stream)
print(manifest["version"], manifest["implementation_commit"],
      "development_build:", manifest["development_build"])
PY
) || fail "the release manifest is missing, malformed, or incomplete"
echo "identity: $identity"

# Note: this deliberately does not try to read the app's flags out of the binary. It did at
# first, and that was wrong twice over - Swift folds those short string literals away, and the
# one occurrence that does survive is the app's own CLI error text ("Missing address for
# --listen-host."), which is not the service command line at all. The unit test in
# tests/native/main.swift pins the argument list; this script pins the outcome.
q=$(xattr -r -p com.apple.quarantine "$APP" 2>/dev/null | grep -c com.apple.quarantine)
echo "quarantined files: $q"
[ "$q" -gt 0 ] && echo "  (clear it with: xattr -cr \"$APP\")"
echo

TMP=$(mktemp -d)
chmod 700 "$TMP"
# The service needs a model-library folder to start; give it an empty throwaway one.
LIB="$TMP/empty-library"
mkdir -p "$LIB/.studiohub"
chmod 700 "$LIB/.studiohub"
# The flags have to be the ones the app passes, or this proves a command line nobody uses -
# which is exactly how a `--listen-host`/`--host` mismatch shipped. The port is one of them now:
# the app pins it so a node's address keeps naming this Mac. When Studio Hub is already running on
# this Mac it holds that port, and a verifier that fought it would fail on its own refusal rather
# than on the copy being checked - so it steps aside and says so. Set PORT to force a value.
PORT="${PORT:-47873}"
if [ "$PORT" != "0" ] && /usr/sbin/lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port $PORT is in use on this Mac (Studio Hub running?); using a system-chosen port instead"
  PORT=0
fi
"$SVC" --data-dir "$TMP" --models-dir "$LIB" --ready-file "$TMP/hub-ready.json" \
       --parent-pid "$$" --host "127.0.0.1" --port "$PORT" > "$TMP/start.log" 2>&1 &
svc=$!
stop() { kill -TERM "$svc" 2>/dev/null; sleep 2; kill -9 "$svc" 2>/dev/null; }
trap stop EXIT

printf 'starting'
i=0
while [ $i -lt 90 ]; do
  if [ -f "$TMP/hub-ready.json" ]; then break; fi
  if ! kill -0 "$svc" 2>/dev/null; then
    echo; echo "--- the service exited immediately. Its own message: ---"
    sed 's/^/  /' "$TMP/start.log"
    fail "the bundled service refused to start (see the message above)"
  fi
  printf '.'
  i=$((i + 1))
  sleep 1
done
echo

if [ ! -f "$TMP/hub-ready.json" ]; then
  echo "--- no readiness record after 90s. Log: ---"; sed 's/^/  /' "$TMP/start.log"
  fail "the service ran but never became ready"
fi

if ! "$PY" - "$TMP/hub-ready.json" <<'PY'
import json, sys, urllib.request
with open(sys.argv[1]) as stream:
    info = json.load(stream)
if not isinstance(info, dict) or not isinstance(info.get("url"), str) or not isinstance(info.get("token"), str):
    raise ValueError("readiness record must contain string url and token values")
req = urllib.request.Request(info["url"].rstrip("/") + "/api/health",
                             headers={"Authorization": "Bearer " + info["token"]})
with urllib.request.urlopen(req, timeout=15) as response:
    body = json.loads(response.read())
    if response.status != 200:
        raise RuntimeError("health endpoint returned HTTP %s" % response.status)
    if not isinstance(body, dict) or body.get("ok") is not True:
        raise ValueError("health response must be a JSON object with ok=true")
    print("ready at:", info["url"], "| health:", response.status, "ok =", body["ok"])
PY
then
  fail "the service readiness or authenticated health check failed"
fi

echo
echo "RESULT: PASS — this copy starts, becomes ready and answers its health check."
echo "You can delete this temporary state; the app uses its own when you launch it normally."
