#!/bin/sh
# Studio Hub KH — start the bundled service by hand and show its real complaint.
#
# The app reports only "engine exited (code 2)". The worker exits 2 through
# argparse, which does print a message - the app just doesn't surface it. This
# runs the same launcher with the same arguments and lets the message through.
#
# Read-only: it inspects state and runs the service in check mode, which starts
# nothing and writes nothing.
#
#   sh ~/Desktop/studiohub-start.sh

APP="/Applications/Studio Hub.app"

# Accept either the version-free bundle name or the older versioned one, and either Applications
# folder, so a Mac carrying a previous kit still works.
if [ ! -d "$APP" ]; then
  for _cand in "/Applications/Studio Hub.app" "/Applications/Studio Hub 0.4.0.app" \
               "$HOME/Applications/Studio Hub.app" "$HOME/Applications/Studio Hub 0.4.0.app"; do
    [ -d "$_cand" ] && { APP="$_cand"; break; }
  done
fi

RES="$APP/Contents/Resources"
DATA="$HOME/Library/Application Support/StudioHub Standalone"

printf 'launcher:  %s\n' "$APP/Contents/MacOS/StudioHubService"
printf 'voice py:  %s\n' "$RES/runtimes/voice/bin/python3"

echo
echo "===== A. the launcher must be executable ====="
if [ -x "$APP/Contents/MacOS/StudioHubService" ]; then
  echo "  ok: executable"
else
  echo "  NOT EXECUTABLE — the app would say the services are missing, not code 2."
  ls -l "$APP/Contents/MacOS/StudioHubService"
fi

echo
echo "===== B. the bundled python must run ====="
"$RES/runtimes/voice/bin/python3" -V 2>&1 | sed 's/^/  /'

echo
echo "===== C. what the app saved ====="
if [ -f "$DATA/app-preferences.json" ]; then
  cat "$DATA/app-preferences.json" | sed 's/^/  /'
  MODELS=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('modelsPath',''))" "$DATA/app-preferences.json" 2>/dev/null)
  LISTEN=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('listenHost','127.0.0.1'))" "$DATA/app-preferences.json" 2>/dev/null)
else
  echo "  (no preferences saved — the app has never started successfully)"
  MODELS=""; LISTEN="127.0.0.1"
fi
echo "  resolved library: ${MODELS:-<none>}"
if [ -n "$MODELS" ]; then
  if [ -d "$MODELS" ]; then echo "  library present:  yes"; else echo "  library present:  NO — this alone stops the worker (code 2)"; fi
fi

echo
echo "===== D. state directory and its permissions ====="
ls -la "$DATA" 2>&1 | sed 's/^/  /'
if [ -d "$DATA/hub" ]; then
  echo "  -- $DATA/hub --"
  ls -ld "$DATA/hub" | sed 's/^/  /'
  echo "  (the worker requires: owner = you, and mode 700 — group/other bits must be zero)"
else
  echo "  $DATA/hub does not exist yet (the worker creates it)"
fi

echo
echo "===== E. running the service in CHECK mode (starts nothing) ====="
echo "  -- this is where the real message appears --"
"$APP/Contents/MacOS/StudioHubService" \
  --data-dir "$DATA" \
  --models-dir "${MODELS:-$DATA/models}" \
  --ready-file "$DATA/hub-ready.json" \
  --listen-host "$LISTEN" \
  --check
echo "  exit code: $?"

echo
echo "===== F. if nothing above complained, watch it do a REAL start for 6s ====="
echo "  (this does start the services, then stops them)"
"$APP/Contents/MacOS/StudioHubService" \
  --data-dir "$DATA" \
  --models-dir "${MODELS:-$DATA/models}" \
  --ready-file "$DATA/hub-ready.json" \
  --listen-host "$LISTEN" >/tmp/studiohub-manual.log 2>&1 &
manual=$!
sleep 6
if kill -0 "$manual" 2>/dev/null; then
  echo "  still running after 6s — the manual start WORKS, so the failure is in how the app launches it"
  kill -TERM "$manual" 2>/dev/null
else
  wait "$manual"; echo "  exited early with code $?"
fi
echo "  -- output --"
tail -40 /tmp/studiohub-manual.log | sed 's/^/  /'

echo
echo "===== done ====="
