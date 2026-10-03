#!/bin/sh
# INSTALL YAM DISPLAY
#
# Double-click this file. It installs Yam Display (the virtual display app that lets a headless Mac
# mini render and be screen-shared) from the installer sitting beside it, `YamDisplay*.dmg`.
#
# It is the same step `1 - Install the app.command` and `Update this Mac.command` run for you; this
# file is for a Mac that is not set up with Studio Hub (the Gateway host, say) or for running just
# that step again.
#
#   - Not installed, or an older version is installed: the app in the installer is copied to
#     Applications. The installer is attached read-only and hidden (nothing appears on the Desktop)
#     and detached again afterwards.
#   - The same or a newer version is installed: nothing is done. It never installs an older one.
#   - No installer beside this file: one line saying so, and nothing else happens.
#   - Anything fails: it says what and exits non-zero. Callers carry on - a Yam Display problem
#     never stops Studio Hub from installing.
#
# Safe to run again. An installed copy is replaced only once the new one has been copied in full; it
# is put back if the swap fails. A running Yam Display is not quit (that would drop the virtual
# display): it keeps running and uses the new version the next time it is opened.
#
# The `STUDIOHUB_*` names are for testing this against scratch folders with a stand-in `hdiutil`;
# nobody sets them in real use.
set -u
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
EXTRAS="${STUDIOHUB_EXTRAS_DIR:-$HERE}"
HDIUTIL="${STUDIOHUB_HDIUTIL:-/usr/bin/hdiutil}"
NAME="Yam Display.app"
UNATTENDED="${STUDIOHUB_UNATTENDED:-}"

if [ -n "${STUDIOHUB_YAMDISPLAY_TARGET:-}" ]; then
  TARGET="$STUDIOHUB_YAMDISPLAY_TARGET"
elif [ ! -d "/Applications/$NAME" ] && [ -d "$HOME/Applications/$NAME" ]; then
  TARGET="$HOME/Applications"          # an own-folder install is updated where it is
else
  TARGET="/Applications"
fi

MNT=""
SWAP=""
cleanup() {
  if [ -n "$MNT" ]; then
    "$HDIUTIL" detach "$MNT" -quiet >/dev/null 2>&1 || "$HDIUTIL" detach "$MNT" -force -quiet >/dev/null 2>&1
    rmdir "$MNT" 2>/dev/null
  fi
  [ -n "$SWAP" ] && rm -f "$SWAP"
  return 0
}
trap cleanup EXIT
trap 'exit 130' INT TERM

finish() {
  if [ "$UNATTENDED" != "1" ]; then
    echo
    echo "  Press Return to close."
    read _ || true
  fi
  exit "$1"
}
fail() {
  echo
  echo "  Yam Display was NOT installed: $1"
  echo "  Nothing else is affected. Run 'Install Yam Display.command' (in the kit's Extras folder) to try again."
  finish 1
}

# The version of the app bundle at $1, or nothing.
app_version() { /usr/bin/plutil -extract CFBundleShortVersionString raw -o - "$1/Contents/Info.plist" 2>/dev/null; }

# True when dotted version $1 is newer than $2 (1.10 > 1.9; a missing part counts as 0).
newer() {
  awk -v a="$1" -v b="$2" 'BEGIN {
    n = split(a, x, "."); m = split(b, y, "."); k = (n > m) ? n : m
    for (i = 1; i <= k; i++) { p = x[i] + 0; q = y[i] + 0; if (p > q) exit 0; if (p < q) exit 1 }
    exit 1 }'
}

[ "$UNATTENDED" = "1" ] || clear
echo "  Yam Display"

# The installer in this kit: the highest version when there are several.
DMG=""
for f in "$EXTRAS"/YamDisplay*.dmg; do
  [ -f "$f" ] || continue
  if [ -z "$DMG" ] || [ "$(printf '%s\n%s\n' "$DMG" "$f" | sort -V | tail -1)" = "$f" ]; then DMG="$f"; fi
done
if [ -z "$DMG" ]; then
  echo "  Yam Display: no installer in this kit (Extras/YamDisplay*.dmg) - skipped."
  finish 0
fi

INSTALLED_VERSION=""
[ -d "$TARGET/$NAME" ] && INSTALLED_VERSION="$(app_version "$TARGET/$NAME")"

# Cheap check from the file name (YamDisplay2.4.8.4.dmg) so a current Mac does not attach anything.
# The version inside the app is what decides before anything is copied.
NAME_VERSION="$(basename "$DMG" | sed -n 's/^YamDisplay\([0-9][0-9.]*[0-9]\).*$/\1/p')"
if [ -n "$INSTALLED_VERSION" ] && [ -n "$NAME_VERSION" ] && ! newer "$NAME_VERSION" "$INSTALLED_VERSION"; then
  echo "  Yam Display $INSTALLED_VERSION is already installed - nothing to do."
  finish 0
fi

MNT="$(mktemp -d -t yamdisplay-installer)" || fail "could not make a scratch folder."
if ! "$HDIUTIL" attach -readonly -nobrowse -noautoopen -mountpoint "$MNT" "$DMG" >/dev/null 2>&1; then
  rmdir "$MNT" 2>/dev/null; MNT=""
  fail "macOS could not open $(basename "$DMG")."
fi
SRC=""
if [ -d "$MNT/$NAME" ]; then SRC="$MNT/$NAME"; else
  for spot in "$MNT"/*.app; do [ -d "$spot" ] && { SRC="$spot"; break; }; done
fi
[ -n "$SRC" ] || fail "$(basename "$DMG") does not contain $NAME."
NEW_VERSION="$(app_version "$SRC")"
[ -n "$NEW_VERSION" ] || fail "could not read the version of the app in $(basename "$DMG")."

if [ -n "$INSTALLED_VERSION" ] && ! newer "$NEW_VERSION" "$INSTALLED_VERSION"; then
  echo "  Yam Display $INSTALLED_VERSION is already installed (the kit has $NEW_VERSION) - nothing to do."
  finish 0
fi
if [ -n "$INSTALLED_VERSION" ]; then
  echo "  Updating Yam Display $INSTALLED_VERSION -> $NEW_VERSION"
elif [ -d "$TARGET/$NAME" ]; then
  echo "  Replacing the Yam Display on this Mac (its version could not be read) with $NEW_VERSION"
else
  echo "  Installing Yam Display $NEW_VERSION"
fi

# Remember whether the old one is running, to say so afterwards. It is never quit.
RUNNING=0
OLD_EXE="$(/usr/bin/plutil -extract CFBundleExecutable raw -o - "$TARGET/$NAME/Contents/Info.plist" 2>/dev/null)"
[ -n "$OLD_EXE" ] && /usr/bin/pgrep -x "$OLD_EXE" >/dev/null 2>&1 && RUNNING=1

# The new copy is made beside the old one and swapped in only when complete. One script so the
# administrator prompt (needed only when Applications is not writable) shows once.
SWAP="$(mktemp -t yamdisplay-swap)"
cat > "$SWAP" <<'STEPS'
#!/bin/sh
# usage: <app in the installer> <target folder> <app name>
SRC="$1"; TARGET="$2"; NAME="$3"
STAGING="$TARGET/.yamdisplay-incoming"
OLD="$TARGET/.yamdisplay-previous"
LIVE="$TARGET/$NAME"
gone() { if [ -e "$1" ]; then chmod -R u+w "$1" 2>/dev/null; rm -rf "$1"; fi; return 0; }
gone "$STAGING"; gone "$OLD"        # leftovers of an interrupted run
/usr/bin/ditto "$SRC" "$STAGING" || { gone "$STAGING"; exit 1; }
[ -f "$STAGING/Contents/Info.plist" ] || { gone "$STAGING"; exit 2; }
/usr/bin/xattr -dr com.apple.quarantine "$STAGING" 2>/dev/null
if [ -e "$LIVE" ]; then mv "$LIVE" "$OLD" || { gone "$STAGING"; exit 3; }; fi
if ! mv "$STAGING" "$LIVE"; then
  gone "$LIVE"
  if [ -e "$OLD" ]; then mv "$OLD" "$LIVE"; fi
  gone "$STAGING"
  exit 4
fi
gone "$OLD"
exit 0
STEPS
if [ -w "$TARGET" ] || [ -n "${STUDIOHUB_YAMDISPLAY_TARGET:-}" ]; then
  /bin/sh "$SWAP" "$SRC" "$TARGET" "$NAME"
else
  /usr/bin/osascript -e "do shell script \"/bin/sh '$SWAP' '$SRC' '$TARGET' '$NAME'\" with administrator privileges" >/dev/null 2>&1
fi
STATUS=$?
[ "$STATUS" = "0" ] || fail "copying it into $TARGET did not work (any Yam Display already there is still there). Check free space and the administrator password."

DONE_VERSION="$(app_version "$TARGET/$NAME")"
[ "$DONE_VERSION" = "$NEW_VERSION" ] || fail "the copy in $TARGET did not check out afterwards."
echo "  Installed Yam Display $DONE_VERSION in $TARGET"
if [ "$RUNNING" = "1" ]; then
  echo "  Yam Display is running: it was left running. Quit and reopen it to use the new version."
fi
finish 0
