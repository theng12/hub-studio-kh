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
# The install target and the measured free space are overridable so the two guards below can be
# proved to fail on their defect (`tests/test_install_local.py`), which is the only way a guard is
# worth having. Production passes neither and gets the real paths.
TARGET="${STUDIOHUB_INSTALL_TARGET:-/Applications/Studio Hub.app}"
DATA="${STUDIOHUB_INSTALL_DATA:-$HOME/Library/Application Support/StudioHub Standalone}"

[ -d "$STAGED" ] || { echo "no staged app at $STAGED"; exit 1; }
STAGED_BUILD=$(/usr/libexec/PlistBuddy -c 'Print :StudioHubBuild' "$STAGED/Contents/Info.plist" 2>/dev/null)
STAGED_VERSION=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$STAGED/Contents/Info.plist" 2>/dev/null)
[ -n "$STAGED_BUILD" ] || { echo "staged app has no StudioHubBuild"; exit 1; }

# **A copy that runs out of space leaves a half-app, and the rollback used to make it worse.**
# Measured 2026-09-26, on a full disk: `cp -Rp` stopped after ~1.4 GB of a 2.9 GB bundle, `rm -rf`
# could not clear the wreckage (see `restore_previous`), and the `mv` put the previous app *inside*
# it. `/Applications/Studio Hub.app` then existed with no `Info.plist` and no `MacOS` at all.
# There is no sensible repair for that at this point in the script, so refuse before touching
# anything the operator would have to reconstruct by hand. **This check also runs before the app is
# quit**: being told there is no room is not a reason to interrupt the app someone is using.
staged_kb=$(du -sk "$STAGED" | cut -f1)
free_kb="${STUDIOHUB_INSTALL_FREE_KB:-$(df -Pk "$(dirname -- "$TARGET")" | awk 'NR == 2 {print $4}')}"
needed_kb=$(( staged_kb + staged_kb / 2 ))
if [ "$free_kb" -lt "$needed_kb" ]; then
  echo "refusing to install: the stage is $(( staged_kb / 1048576 )) GB and needs $(( needed_kb / 1048576 )) GB free,"
  echo "but $(dirname -- "$TARGET") has $(( free_kb / 1048576 )) GB. Free space first - a copy that fills"
  echo "the disk leaves a bundle with no Info.plist and no MacOS."
  exit 1
fi

echo "== stopping any running copy"
# `quit app "Studio Hub"` matches by *name*, so it can only ever mean the installed app: run it
# only when that is what we are replacing. The kill loop below is matched on the target path for the
# same reason. Without both, an install into a temp path would interrupt the app someone is using -
# which is exactly what the guard tests do.
if [ "$TARGET" = "/Applications/Studio Hub.app" ]; then
  osascript -e 'quit app "Studio Hub"' >/dev/null 2>&1 || true
  sleep 3
fi
# Matched on **this** target, not on the app's name: an install into a different path must not quit
# or kill the copy that is actually running, which is also what keeps the guard tests above from
# touching the operator's app.
for attempt in 1 2 3; do
  pids=$(pgrep -f "$TARGET/Contents" || true)
  [ -z "$pids" ] && break
  kill $pids 2>/dev/null || true
  sleep 2
done
pids=$(pgrep -f "$TARGET/Contents" || true)
if [ -n "$pids" ]; then kill -9 $pids 2>/dev/null || true; sleep 2; fi

restore_previous() {
  echo "$1; restoring the previous app"
  # **`rm -rf` cannot delete the image runtime's read-only tree on its own.** The packaged
  # `runtimes/image/lib/tcl9` directories are `dr-xr-xr-x`, and unlinking an entry needs write
  # permission on its *parent* directory, so the tree survives and every directory above it reports
  # "Directory not empty". `chmod -R u+w` first, and if the path still exists afterwards, stop and
  # say so: the `mv` below would otherwise move the previous app into the wreckage rather than over
  # it, which is a state no later step can detect.
  chmod -R u+w "$TARGET" 2>/dev/null || true
  rm -rf "$TARGET" 2>/dev/null || true
  if [ -e "$TARGET" ]; then
    echo "FAILED: could not clear $TARGET. The previous app is intact at $OLD - move it back by hand."
    exit 1
  fi
  [ -e "$OLD" ] && mv "$OLD" "$TARGET"
  exit 1
}

echo "== swapping in $STAGED_VERSION / $STAGED_BUILD"
OLD="$(dirname -- "$TARGET")/.studiohub-old-$$"
if [ -e "$TARGET" ]; then mv "$TARGET" "$OLD" || { echo "could not move the old app aside"; exit 1; }; fi
if ! cp -Rp "$STAGED" "$TARGET"; then
  restore_previous "copy failed"
fi
if [ ! -x "$TARGET/Contents/Resources/runtimes/voice/bin/python3" ]; then
  restore_previous "the copy arrived incomplete"
fi
# One file is not a completeness check: a copy that stopped inside `runtimes/` still has the voice
# interpreter, which is exactly how a bundle with no `Info.plist` passed the old guard. Count
# entries instead - it costs a `find` and it catches any partial copy.
staged_entries=$(find "$STAGED" | wc -l | tr -d ' ')
target_entries=$(find "$TARGET" | wc -l | tr -d ' ')
if [ "$staged_entries" != "$target_entries" ]; then
  restore_previous "the copy arrived incomplete ($target_entries of $staged_entries entries)"
fi
if [ -e "$OLD" ]; then
  chmod -R u+w "$OLD" 2>/dev/null || true
  rm -rf "$OLD" 2>/dev/null || true
  [ -e "$OLD" ] && echo "note: the previous app is still at $OLD (read-only runtime files); harmless, move it to _trash/ by hand"
fi

echo "== checking what actually landed"
INSTALLED_BUILD=$(/usr/libexec/PlistBuddy -c 'Print :StudioHubBuild' "$TARGET/Contents/Info.plist" 2>/dev/null)
if [ "$INSTALLED_BUILD" != "$STAGED_BUILD" ]; then
  echo "FAILED: $TARGET has build '$INSTALLED_BUILD', expected '$STAGED_BUILD'"
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
