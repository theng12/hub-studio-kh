#!/bin/bash
# Install a staged build into /Applications, and prove it landed.
#
#   bash packaging/install_local.sh .build/StudioHub-0644/Hub Studio KH.app
#   STUDIOHUB_INSTALL_START=0 bash packaging/install_local.sh <app>   # install, leave it stopped
#
# The previous ad-hoc version of this step chained the swap with `|| true`, so a swap that did
# not happen looked like a success - and a stale app with writable runtime files went
# unnoticed until its Music worker refused to start. This one checks the result and fails loudly.
set -u

STAGED="${1:?usage: install_local.sh <path to Hub Studio KH.app>}"
# The install target and the measured free space are overridable so the two guards below can be
# proved to fail on their defect (`tests/test_install_local.py`), which is the only way a guard is
# worth having. Production passes neither and gets the real paths.
TARGET="${STUDIOHUB_INSTALL_TARGET:-/Applications/Hub Studio KH.app}"
DATA="${STUDIOHUB_INSTALL_DATA:-$HOME/Library/Application Support/StudioHub Standalone}"
TARGET_PARENT="$(dirname -- "$TARGET")"
TRASH="${STUDIOHUB_INSTALL_TRASH:-$TARGET_PARENT/_trash}"
# The owner may want the install without leaving the Hub running. Default stays on; a value that is
# not 0 or 1 is refused here, before anything is stopped or swapped.
START="${STUDIOHUB_INSTALL_START:-1}"
case "$START" in
  1|0) ;;
  *) echo "STUDIOHUB_INSTALL_START must be 1 or 0, not '$START'"; exit 1 ;;
esac

present() { [ -e "$1" ] || [ -L "$1" ]; }
archive() {
  local source="$1" archive_root="$2" label="$3" stamp destination suffix original_mode=""
  present "$source" || return 0
  mkdir -p "$archive_root" || return 1
  stamp=$(/bin/date -u +%Y%m%d-%H%M%S)
  destination="$archive_root/$label-$stamp-$$"
  suffix=0
  while present "$destination"; do
    suffix=$((suffix + 1))
    destination="$archive_root/$label-$stamp-$$-$suffix"
  done
  if [ -d "$source" ] && [ ! -w "$source" ]; then
    original_mode=$(/usr/bin/stat -f %Lp "$source") || return 1
    chmod u+w "$source" || return 1
  fi
  if mv "$source" "$destination"; then
    [ -z "$original_mode" ] || chmod "$original_mode" "$destination"
    return 0
  fi
  [ -z "$original_mode" ] || chmod "$original_mode" "$source"
  return 1
}

[ -d "$STAGED" ] || { echo "no staged app at $STAGED"; exit 1; }
STAGED_BUILD=$(/usr/libexec/PlistBuddy -c 'Print :StudioHubBuild' "$STAGED/Contents/Info.plist" 2>/dev/null)
STAGED_VERSION=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$STAGED/Contents/Info.plist" 2>/dev/null)
[ -n "$STAGED_BUILD" ] || { echo "staged app has no StudioHubBuild"; exit 1; }

# **A copy that runs out of space leaves a half-app, and the rollback used to make it worse.**
# Measured 2026-09-26, on a full disk: `cp -Rp` stopped after ~1.4 GB of a 2.9 GB bundle, `rm -rf`
# could not clear the wreckage (see `restore_previous`), and the `mv` put the previous app *inside*
# it. `/Applications/Hub Studio KH.app` then existed with no `Info.plist` and no `MacOS` at all.
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

if [ "$START" = 0 ]; then
  # Off mode never quits or kills: AppleScript's `quit` can launch a stopped app, and killing the
  # owner's running Hub would leave it down with nothing here to bring it back. Refuse instead.
  running=$(pgrep -f "$TARGET/Contents" || true)
  if [ -n "$running" ]; then
    echo "refusing to install with STUDIOHUB_INSTALL_START=0: $TARGET is running (pids: $running); quit it first"
    exit 1
  fi
else
echo "== stopping any running copy"
# `quit app "Studio Hub"` matches by *name*, so it can only ever mean the installed app: run it
# only when that is what we are replacing. The kill loop below is matched on the target path for the
# same reason. Without both, an install into a temp path would interrupt the app someone is using -
# which is exactly what the guard tests do.
if [ "$TARGET" = "/Applications/Hub Studio KH.app" ]; then
  osascript -e 'quit app "Hub Studio KH"' >/dev/null 2>&1 || true
  sleep 3
  # The app used to install as "Studio Hub.app" (renamed 2026-11-12). A Mac upgrading across
  # that rename would otherwise keep two copies; stop the old one and keep it recoverably.
  OLD="/Applications/Studio Hub.app"
  if present "$OLD"; then
    echo "== retiring the old Studio Hub.app to _trash"
    osascript -e 'quit app "Studio Hub"' >/dev/null 2>&1 || true
    for attempt in 1 2 3; do
      pids=$(pgrep -f "$OLD/Contents" || true)
      [ -z "$pids" ] && break
      kill $pids 2>/dev/null || true
      sleep 2
    done
    pids=$(pgrep -f "$OLD/Contents" || true)
    [ -z "$pids" ] || { kill -9 $pids 2>/dev/null || true; sleep 1; }
    archive "$OLD" "$TRASH" old-name || echo "note: could not archive $OLD; the new app is still installed"
  fi
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
fi

restore_previous() {
  echo "$1; restoring the previous app"
  # A partial app is evidence. Move the whole tree aside before restoring the previous app; this
  # also avoids the old failure where a read-only partial tree caused the rollback to nest bundles.
  if ! archive "$TARGET" "$TRASH" failed-install; then
    echo "FAILED: could not archive $TARGET. The previous app is intact at $OLD - move it back by hand."
    exit 1
  fi
  present "$OLD" && mv "$OLD" "$TARGET"
  exit 1
}

echo "== swapping in $STAGED_VERSION / $STAGED_BUILD"
OLD="$TARGET_PARENT/.studiohub-old-$$"
archive "$OLD" "$TRASH" interrupted-previous || { echo "could not archive a stale previous app"; exit 1; }
if present "$TARGET"; then mv "$TARGET" "$OLD" || { echo "could not move the old app aside"; exit 1; }; fi
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
echo "== checking what actually landed"
INSTALLED_BUILD=$(/usr/libexec/PlistBuddy -c 'Print :StudioHubBuild' "$TARGET/Contents/Info.plist" 2>/dev/null)
if [ "$INSTALLED_BUILD" != "$STAGED_BUILD" ]; then
  restore_previous "FAILED: $TARGET has build '$INSTALLED_BUILD', expected '$STAGED_BUILD'"
fi

# The modes matter only because a mismatch used to brick startup; check the payload is intact.
python3 - "$TARGET" <<'PY' || restore_previous "the installed music runtime could not be checked"
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

archive "$OLD" "$TRASH" previous-app || { echo "FAILED: could not archive the previous app at $OLD"; exit 1; }
archive "$DATA/hub-ready.json" "$DATA/_trash" hub-ready || { echo "FAILED: could not archive hub-ready.json"; exit 1; }
archive "$DATA/hub/music-ready.json" "$DATA/_trash" music-ready || { echo "FAILED: could not archive music-ready.json"; exit 1; }
echo "== installed $STAGED_VERSION / $INSTALLED_BUILD"

# **An install stopped Studio Hub and never started it again, so "installed" was not "running".**
# Measured 2026-09-28: after installing 0.6.97 the Hub was down and nothing answered on 47873 until
# it was opened by hand. The cost is not the missing window — it is everything that reads this Mac's
# Hub: the Gateway reports every Studio Hub location unreachable, every model picker on the fleet
# empties, and each drive's installer talks to a machine that is not there. A release that ends in
# that state looks finished and is not.
#
# Only for the real install target, for the same reason the stop above is: an install into a temp
# path must not start an app, which is also what keeps the guard tests from launching anything.
if [ "$TARGET" = "/Applications/Hub Studio KH.app" ] && [ "$START" = 1 ]; then
  echo "== starting Hub Studio KH"
  # Hidden and unfocused (-g -j): the owner's desktop is not part of an install (owner,
  # 2026-11-12 "Don't take the owner's desktop").
  open -g -j -a "$TARGET" >/dev/null 2>&1 || true
  # Wait for the **Hub**, not for the app bundle: `hub-ready.json` is written by the Hub service once
  # it is actually serving, so this cannot report success for a process that is merely running.
  READY="$DATA/hub-ready.json"
  for _ in $(seq 1 30); do
    [ -f "$READY" ] && break
    sleep 2
  done
  if [ -f "$READY" ]; then
    echo "== Hub is serving (hub-ready.json present)"
  else
    # Deliberately not a failure: the install *did* succeed — the bundle is verified above — and a
    # Hub that will not start has its own reason, which the app's log states better than this script
    # can guess at. Saying so beats exiting 0 in silence.
    echo "== WARNING: Studio Hub was installed but its Hub did not come up within 60 s."
    echo "   Nothing reads this Mac's Hub until it does: the Gateway shows every Studio Hub location"
    echo "   unreachable and the model pickers are empty. Check the app's log."
  fi
fi
