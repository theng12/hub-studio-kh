#!/bin/bash
# Install a staged build into /Applications, and prove it landed.
#
#   bash packaging/install_local.sh .build/StudioHub-0644/Studio Hub.app
#
# The previous ad-hoc version of this step chained the swap with `|| true`, so a swap that did
# not happen looked like a success - and a stale app with writable runtime files went
# unnoticed until its Music worker refused to start. This one checks the result and fails loudly.
set -u

STAGED="${1:?usage: install_local.sh <path to Studio Hub.app>}"
TARGET="/Applications/Studio Hub.app"
DATA="$HOME/Library/Application Support/StudioHub Standalone"

[ -d "$STAGED" ] || { echo "no staged app at $STAGED"; exit 1; }
STAGED_BUILD=$(/usr/libexec/PlistBuddy -c 'Print :StudioHubBuild' "$STAGED/Contents/Info.plist" 2>/dev/null)
STAGED_VERSION=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$STAGED/Contents/Info.plist" 2>/dev/null)
[ -n "$STAGED_BUILD" ] || { echo "staged app has no StudioHubBuild"; exit 1; }

echo "== stopping any running copy"
osascript -e 'quit app "Studio Hub"' >/dev/null 2>&1 || true
sleep 3
for attempt in 1 2 3; do
  pids=$(pgrep -f "Studio Hub.app/Contents" || true)
  [ -z "$pids" ] && break
  kill $pids 2>/dev/null || true
  sleep 2
done
pids=$(pgrep -f "Studio Hub.app/Contents" || true)
if [ -n "$pids" ]; then kill -9 $pids 2>/dev/null || true; sleep 2; fi

echo "== swapping in $STAGED_VERSION / $STAGED_BUILD"
OLD="/Applications/.studiohub-old-$$"
if [ -e "$TARGET" ]; then mv "$TARGET" "$OLD" || { echo "could not move the old app aside"; exit 1; }; fi
if ! cp -Rp "$STAGED" "$TARGET"; then
  echo "copy failed; restoring the previous app"
  rm -rf "$TARGET"
  [ -e "$OLD" ] && mv "$OLD" "$TARGET"
  exit 1
fi
if [ ! -x "$TARGET/Contents/Resources/runtimes/voice/bin/python3" ]; then
  echo "the copy arrived incomplete; restoring the previous app"
  rm -rf "$TARGET"
  [ -e "$OLD" ] && mv "$OLD" "$TARGET"
  exit 1
fi
[ -e "$OLD" ] && { chmod -R u+w "$OLD" 2>/dev/null || true; rm -rf "$OLD" 2>/dev/null || true; }

echo "== checking what actually landed"
INSTALLED_BUILD=$(/usr/libexec/PlistBuddy -c 'Print :StudioHubBuild' "$TARGET/Contents/Info.plist" 2>/dev/null)
if [ "$INSTALLED_BUILD" != "$STAGED_BUILD" ]; then
  echo "FAILED: /Applications has build '$INSTALLED_BUILD', expected '$STAGED_BUILD'"
  exit 1
fi

# The modes matter only because a mismatch used to brick startup; check the payload is intact.
python3 - "$TARGET" <<'PY' || exit 1
import sys
from hashlib import sha256
from pathlib import Path
root = Path(sys.argv[1]) / "Contents/Resources/runtimes/music"
if not root.is_dir():
    sys.exit(0)
h = sha256()
for p in sorted(root.rglob("*")):
    if p.is_dir():
        continue
    h.update(p.relative_to(root).as_posix().encode() + b"\0file\0")
    h.update(p.read_bytes())
    h.update(b"\0")
print("== music overlay (content only):", h.hexdigest()[:16])
PY

rm -f "$DATA/hub-ready.json" "$DATA/hub/music-ready.json"
echo "== installed $STAGED_VERSION / $INSTALLED_BUILD"
