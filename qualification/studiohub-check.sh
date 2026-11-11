#!/bin/sh
# Studio Hub KH — startup diagnosis.
#
# Read-only: this inspects the installed app and its state, and prints what it
# finds. It changes nothing and deletes nothing.
#
#   sh ~/Desktop/studiohub-check.sh > ~/Desktop/studiohub-check.txt 2>&1
#
# Then send back studiohub-check.txt.

APP="/Applications/Hub Studio KH.app"

# Accept either the version-free bundle name or the older versioned one, and either Applications
# folder, so a Mac carrying a previous kit still works.
if [ ! -d "$APP" ]; then
  for _cand in "/Applications/Hub Studio KH.app" "/Applications/Studio Hub 0.4.0.app" \
               "$HOME/Applications/Hub Studio KH.app" "$HOME/Applications/Studio Hub 0.4.0.app"; do
    [ -d "$_cand" ] && { APP="$_cand"; break; }
  done
fi

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
echo "===== 6b. WHAT THE APP SEES — models and packages ====="
if [ -f "$DATA/app-preferences.json" ]; then
  # The app's own python: macOS 14 has no system python3, and asking for one pops a
  # developer-tools prompt that has nothing to do with this app.
  M=$("$RES/runtimes/voice/bin/python3" -c '
import json,sys
print(json.load(open(sys.argv[1])).get("modelsPath",""))
' "$DATA/app-preferences.json" 2>/dev/null)
else
  M=""
fi
if [ -n "$M" ] && [ -d "$M" ]; then
  echo "  library: $M"
  echo "  packages present in the library:"
  for d in "$M"/*/; do
    name=$(basename "$d")
    rev=$(ls "$d" 2>/dev/null | head -1)
    printf "    %-34s %s\n" "$name" "${rev:0:12}"
  done
  echo
  echo "  the app's own index of installed packages:"
  if [ -f "$M/.studiohub/installed.json" ]; then
    "$RES/runtimes/voice/bin/python3" -c '
import json,sys
d=json.load(open(sys.argv[1]))
print("    ", len(d), "entries:", ", ".join(sorted(d)))
' "$M/.studiohub/installed.json" 2>&1 | sed 's/^/  /'
  else
    echo "    MISSING: $M/.studiohub/installed.json — without it every package reads as not installed"
  fi
  echo
  echo "  EXPECTED vs PRESENT size per package (a partial copy shows up here):"
  "$RES/runtimes/voice/bin/python3" -c '
import json, sys
from pathlib import Path
res, lib = Path(sys.argv[1]), Path(sys.argv[2])
manifests = {}
for path in sorted((res / "model-manifests").glob("*.json")):
    m = json.loads(path.read_text())
    manifests[m["package_id"]] = m
for pid, m in sorted(manifests.items()):
    expected = sum(int(f.get("size") or 0) for f in m.get("files", []))
    folder = lib / pid
    present = 0
    revision = m["revision"]
    target = folder / revision
    base = target if target.is_dir() else folder
    if base.is_dir():
        for path in base.rglob("*"):
            if path.is_file() and not path.is_symlink():
                present += path.stat().st_size
    flag = "OK" if expected and present >= expected * 0.98 else "INCOMPLETE"
    print("    %-34s %6.2f GB expected  %6.2f GB present  %s"
          % (pid, expected / 1e9, present / 1e9, flag))
' "$RES" "$M"

  echo
  echo "  what each package verifies as (this is what the Models screen shows):"
  "$RES/runtimes/voice/bin/python3" -c '
import json, sys
from pathlib import Path
res, lib = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(res))
try:
    from components import model_packages
except Exception as exc:
    print("    cannot load the packaged model code:", exc); raise SystemExit
manifests = {}
for path in sorted((res / "model-manifests").glob("*.json")):
    m = json.loads(path.read_text())
    manifests[m["package_id"]] = m
print("    manifests found in the app:", len(manifests))
for pid, m in sorted(manifests.items()):
    try:
        r = model_packages.preflight(m, lib, catalog=manifests)
        state = r.get("state")
    except Exception as exc:
        state = "ERROR %s: %s" % (type(exc).__name__, exc)
    print("    %-34s %s" % (pid, state))
' "$RES" "$M"
else
  echo "  no readable library path saved yet — open the app and choose the folder first"
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
