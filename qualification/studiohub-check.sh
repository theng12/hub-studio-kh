#!/bin/sh
# Studio Hub KH — startup diagnosis.
#
# Read-only: this inspects the installed app and its state, and prints what it
# finds. It changes nothing and deletes nothing.
#
#   sh ~/Desktop/studiohub-check.sh > ~/Desktop/studiohub-check.txt 2>&1
#
# Then send back studiohub-check.txt.

APP="/Applications/Studio Hub 0.4.0.app"
DATA="$HOME/Library/Application Support/StudioHub Standalone"
RES="$APP/Contents/Resources"

echo "===== 1. identity ====="
echo "date:      $(date)"
echo "macOS:     $(sw_vers -productVersion) ($(sw_vers -buildVersion))"
echo "hardware:  $(sysctl -n machdep.cpu.brand_string 2>/dev/null)"
echo "memory:    $(( $(sysctl -n hw.memsize) / 1073741824 )) GB"
echo "app path:  $APP"

echo
echo "===== 2. is the app complete? ====="
for f in \
  "Contents/MacOS/StudioHub" \
  "Contents/MacOS/StudioHubService" \
  "Contents/Resources/runtimes/voice/bin/python3" \
  "Contents/Resources/runtimes/image/bin/python3" \
  "Contents/Resources/hub_worker.py" \
  "Contents/Resources/voice_worker.py" \
  "Contents/Resources/image_worker.py" \
  "Contents/Resources/subtitle_worker.py"
do
  if [ -e "$APP/$f" ]; then
    if [ -x "$APP/$f" ]; then perm="executable"; else perm="NOT EXECUTABLE"; fi
    printf "  OK    %-52s %s\n" "$f" "$perm"
  else
    printf "  MISSING %s\n" "$f"
  fi
done

echo
echo "===== 3. quarantine flags on the app ====="
q=$(xattr -r -p com.apple.quarantine "$APP" 2>/dev/null | grep -c com.apple.quarantine)
echo "  files still quarantined: $q"

echo
echo "===== 4. do the bundled runtimes run? ====="
echo "-- voice runtime --"
"$RES/runtimes/voice/bin/python3" -V 2>&1 | sed 's/^/  /'
echo "-- image runtime --"
"$RES/runtimes/image/bin/python3" -V 2>&1 | sed 's/^/  /'

echo
echo "===== 5. state directory ====="
echo "  path: $DATA"
if [ -d "$DATA" ]; then
  ls -la "$DATA" | sed 's/^/  /'
else
  echo "  (does not exist — the app has never got far enough to create it)"
fi

echo
echo "===== 6. preferences the app saved ====="
if [ -f "$DATA/app-preferences.json" ]; then
  cat "$DATA/app-preferences.json" | sed 's/^/  /'
else
  echo "  (no preferences saved yet)"
fi

echo
echo "===== 7. THE LOG — this is the important part ====="
if [ -f "$DATA/hub-worker.log" ]; then
  echo "  size: $(wc -c < "$DATA/hub-worker.log") bytes"
  echo "  ---------- last 120 lines ----------"
  tail -120 "$DATA/hub-worker.log" | sed 's/^/  /'
else
  echo "  NO LOG FILE. The bundled service never got far enough to write one."
  echo "  Check for these instead:"
  for extra in "$DATA"/*.log "$DATA"/*ready*.json; do
    [ -e "$extra" ] && echo "    found: $extra"
  done
  echo "  recent system messages mentioning Studio Hub:"
  log show --last 30m --predicate 'process CONTAINS "StudioHub" OR eventMessage CONTAINS "StudioHub"' \
    --style compact 2>/dev/null | tail -40 | sed 's/^/    /'
fi

echo
echo "===== done ====="
