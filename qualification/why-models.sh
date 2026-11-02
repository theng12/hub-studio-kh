#!/bin/sh
# Why are Image / Subtitle models missing?  Uses the app's OWN python - nothing to install.
#
#   sh "/Volumes/KH-STUDIO-ATLAS/1 Install Apps/Hub Studio KH/why-models.sh"
#
# Reads only. Writes nothing except this output.

APP="${APP:-/Applications/Studio Hub.app}"

# Accept either the version-free bundle name or the older versioned one, and either Applications
# folder, so a Mac carrying a previous kit still works.
if [ ! -d "$APP" ]; then
  for _cand in "/Applications/Studio Hub.app" "/Applications/Studio Hub 0.4.0.app" \
               "$HOME/Applications/Studio Hub.app" "$HOME/Applications/Studio Hub 0.4.0.app"; do
    [ -d "$_cand" ] && { APP="$_cand"; break; }
  done
fi

LIB="${LIB:-}"
PY="$APP/Contents/Resources/runtimes/voice/bin/python3"
RES="$APP/Contents/Resources"
DATA="$HOME/Library/Application Support/StudioHub Standalone"
PREFS="$DATA/app-preferences.json"

say() { printf '%s\n' "$*"; }

say "===== A. WHICH BUILD IS INSTALLED? ====="
if [ -x "$PY" ]; then
  "$PY" -c '
import json,sys
try:
    d=json.load(open(sys.argv[1]))
    print("  version:          ", d.get("version"))
    print("  implementation:   ", d.get("implementation_commit"))
    print("  development_build:", d.get("development_build"))
    print("  newest_build_is:   5388203380003291e260c471b0a3e5f981349eef")
except Exception as exc:
    print("  cannot read the app release manifest:", exc)
' "$RES/release-manifest.json"
else
  say "  the app is not at $APP"
fi

say ""
say "===== B. WHAT LIBRARY IS THE APP POINTED AT? ====="
if [ -f "$PREFS" ] && [ -x "$PY" ]; then
  LIB_SAVED=$("$PY" -c '
import json,sys
print(json.load(open(sys.argv[1])).get("modelsPath",""))
' "$PREFS")
  say "  saved path: $LIB_SAVED"
  if [ -z "$LIB" ]; then LIB="$LIB_SAVED"; fi
  if [ -d "$LIB" ]; then say "  exists:     yes"; else say "  exists:     NO — this alone stops every model"; fi
else
  say "  no preferences saved yet, or the app is missing"
fi

say ""
say "===== C. WHAT IS IN THAT LIBRARY ====="
if [ -n "$LIB" ] && [ -d "$LIB" ]; then
  for d in "$LIB"/*/; do
    name=$(basename "$d")
    n=$(find "$d" -type f 2>/dev/null | wc -l | tr -d ' ')
    sz=$(du -sh "$d" 2>/dev/null | cut -f1)
    printf '  %-34s %6s  %s files\n' "$name" "$sz" "$n"
  done
else
  say "  (no readable library)"
fi

say ""
say "===== D. DOES EACH PACKAGE MATCH ITS RECORD? ====="
if [ -x "$PY" ] && [ -n "$LIB" ] && [ -d "$LIB" ]; then
  "$PY" -c '
import json, sys
from pathlib import Path
res, lib = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(res))
manifests = {}
for path in sorted((res / "model-manifests").glob("*.json")):
    m = json.loads(path.read_text())
    manifests[m["package_id"]] = m
print("  model definitions in this app:", len(manifests))
try:
    from components import model_packages
except Exception as exc:
    print("  cannot load the app model code:", exc); raise SystemExit
for pid, m in sorted(manifests.items()):
    expected = sum(int(f.get("size") or 0) for f in m.get("files", []))
    target = lib / pid / m["revision"]
    base = target if target.is_dir() else lib / pid
    present = 0
    if base.is_dir():
        for p in base.rglob("*"):
            if p.is_file() and not p.is_symlink():
                present += p.stat().st_size
    size_flag = "OK" if expected and present >= expected * 0.98 else "INCOMPLETE"
    try:
        state = model_packages.preflight(m, lib, catalog=manifests).get("state")
    except Exception as exc:
        state = "ERROR %s: %s" % (type(exc).__name__, exc)
    print("  %-34s %6.2f/%-6.2f GB  %-10s %s"
          % (pid, present / 1e9, expected / 1e9, size_flag, state))
' "$RES" "$LIB"
fi

say ""
say "===== E. WHAT THE HUB SAW, FROM ITS OWN LOG ====="
if [ -f "$DATA/hub-worker.log" ]; then
  say "  -- worker startup and any failure --"
  grep -iE "worker|error|traceback|refus|exit|unavailable|subtitle|image" "$DATA/hub-worker.log" \
    | grep -viE "GET /|POST /" | tail -30 | sed 's/^/  /'
else
  say "  no log yet"
fi

say ""
say "===== done ====="
