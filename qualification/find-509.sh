#!/bin/sh
# What returned 509? The Hub logs every request with its status.
#
#   sh "/Volumes/KH-STUDIO-ATLAS/Studio Hub App/find-509.sh"
#
# Reads only.

LOG="$HOME/Library/Application Support/StudioHub Standalone/hub-worker.log"
if [ ! -f "$LOG" ]; then echo "No log yet at $LOG"; exit 1; fi

echo "===== any 5xx the Hub answered ====="
grep -E '" 5[0-9][0-9] ' "$LOG" | tail -25
echo
echo "===== the artifact and audio requests around them ====="
grep -E 'artifact|/audio|ack' "$LOG" | tail -25
echo
echo "===== every request the Hub could not serve ====="
grep -viE '" 2[0-9][0-9] ' "$LOG" | grep -E 'HTTP/1.1" [0-9]' | tail -25
echo
echo "===== worker complaints ====="
grep -iE "error|traceback|refus|unavailable|insufficient|space|disk" "$LOG" | grep -viE 'HTTP/1.1" 2' | tail -20
echo
echo "===== free space where output is written ====="
df -h "$HOME/Library/Application Support/StudioHub Standalone" | tail -1
echo
echo "===== disk-protection state (the app keeps a 50 GB / 30-day budget) ====="
APP="${APP:-/Applications/Studio Hub 0.4.0.app}"
PY="$APP/Contents/Resources/runtimes/voice/bin/python3"
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
[ -x "$PY" ] || PY="$HERE/Studio Hub 0.4.0.app/Contents/Resources/runtimes/voice/bin/python3"
if [ -x "$PY" ]; then
  "$PY" - <<'PYEOF'
import shutil
from pathlib import Path
data = Path.home() / "Library/Application Support/StudioHub Standalone"
total = 0
for root in (data / "voice", data / "image", data / "subtitle"):
    if root.is_dir():
        for p in root.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
print("  generated output on disk: %.2f GB" % (total / 1e9))
free = shutil.disk_usage(data).free if data.exists() else 0
print("  free on that volume:      %.1f GB" % (free / 1e9))
PYEOF
fi
echo
echo "===== done ====="
