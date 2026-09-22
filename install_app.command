#!/bin/sh
# 1 - INSTALL THE APP
#
# Double-click this file. It copies Studio Hub onto this Mac.
# Nothing to type, nothing to configure.

set -u
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
KIT="$(dirname -- "$HERE")"
# This launcher sits either in the kit root or in its Technical folder; the app is beside one of
# them.
APP=""
for spot in "$HERE/Studio Hub.app" "$KIT/Studio Hub.app" \
            "$HERE/Studio Hub 0.4.0.app" "$KIT/Studio Hub 0.4.0.app"; do
  [ -d "$spot" ] && { APP="$spot"; break; }
done
APP="${APP:-$HERE/Studio Hub.app}"
# The installed name carries no version, so a new release replaces the old app instead of
# leaving a second copy beside it.
NAME="Studio Hub.app"

clear
echo "============================================================"
echo "  Studio Hub - installing the app"
echo "============================================================"
echo

if [ ! -d "$APP" ]; then
  echo "  I cannot find the app next to this file."
  echo "  Expected it at:  $APP"
  echo
  echo "  Copy the whole kit folder to the Mac, then run this again."
  echo
  echo "  Press Return to close."
  read _ || true
  exit 1
fi

# Applications needs an administrator password. If that is a problem, install for this user only,
# which needs no password and works just as well.
TARGET="/Applications"
NEEDS_ADMIN=1
if [ -w /Applications ]; then NEEDS_ADMIN=0; fi

echo "  This will replace any Studio Hub already installed."
echo
printf "  Install into Applications (needs your password)? [Y/n] "
read answer || answer=""
case "$answer" in
  n|N)
    TARGET="$HOME/Applications"
    NEEDS_ADMIN=0
    mkdir -p "$TARGET"
    echo "  Installing into your own Applications folder instead - no password needed."
    ;;
esac

echo
echo "  Copying... (this takes a few seconds)"
# A running copy holds its own files open, and replacing one underneath itself is how an install
# half-lands - the old process keeps serving from deleted files while the folder on disk changes.
osascript -e 'quit app "Studio Hub"' >/dev/null 2>&1 || true
osascript -e 'quit app "StudioHub"' >/dev/null 2>&1 || true
sleep 2
# The app ships read-only runtimes, so a plain `rm -rf` of an older install fails with
# "Permission denied" and leaves the old copy in place - the swap then nests the new app inside it.
# Make the tree writable first, and refuse to swap unless the old copy is really gone.
STAGING="$TARGET/.studiohub-incoming"
rm -rf "$STAGING" 2>/dev/null
REMOVE_OLD="if [ -e '$TARGET/$NAME' ]; then chmod -R u+w '$TARGET/$NAME' 2>/dev/null || true; rm -rf '$TARGET/$NAME'; fi"
REPLACE="$REMOVE_OLD && test ! -e '$TARGET/$NAME' && mv '$STAGING' '$TARGET/$NAME'"
if [ "$NEEDS_ADMIN" = "1" ]; then
  # One administrator prompt, the normal macOS way.
  if ! osascript -e "do shell script \"rm -rf '$STAGING' && /bin/cp -Rp '$APP' '$STAGING' && test -x '$STAGING/Contents/Resources/runtimes/voice/bin/python3' && $REPLACE\" with administrator privileges" 2>/dev/null; then
    echo
    echo "  That did not work, and nothing was changed: the copy is made beside the installed app"
    echo "  and only swapped in once it is complete."
    echo
    echo "  Try again and enter your password, or run this file again and answer 'n' to install"
    echo "  into your own Applications folder."
    echo
    echo "  Press Return to close."
    read _ || true
    exit 1
  fi
else
  if ! /bin/cp -Rp "$APP" "$STAGING" \
     || [ ! -x "$STAGING/Contents/Resources/runtimes/voice/bin/python3" ]; then
    echo
    echo "  The copy failed or arrived incomplete. This Mac still has whatever was installed"
    echo "  before; nothing was replaced."
    echo
    echo "  Check free space, then run this file again."
    echo
    echo "  Press Return to close."
    rm -rf "$STAGING" 2>/dev/null
    read _ || true
    exit 1
  fi
  if [ -e "$TARGET/$NAME" ]; then
    chmod -R u+w "$TARGET/$NAME" 2>/dev/null || true
    rm -rf "$TARGET/$NAME" 2>/dev/null || true
  fi
  if [ -e "$TARGET/$NAME" ]; then
    echo "  Could not remove the previously installed copy, so nothing was replaced."
    echo "  Close Studio Hub, then run this file again."
    echo
    rm -rf "$STAGING" 2>/dev/null
    read _ || true
    exit 1
  fi
  mv "$STAGING" "$TARGET/$NAME" || {
    echo "  Could not move the new copy into place. The previous app is gone; run this again."
    read _ || true
    exit 1
  }
fi

# macOS blocks an unsigned app the first time. Clear the flag so a double-click just works.
xattr -cr "$TARGET/$NAME" 2>/dev/null || true

# The runtime was checked before the swap, so this only reads what arrived.
VERSION=$(/usr/bin/plutil -extract CFBundleShortVersionString raw \
          "$TARGET/$NAME/Contents/Info.plist" 2>/dev/null || echo "unknown")

echo
echo "  Installed Studio Hub $VERSION"
echo "  Installed to:  $TARGET/$NAME"
echo
echo "  NEXT:  open it once (right-click it and choose Open), then double-click"
echo "         '2 - Copy the models.command' to bring over the models."
echo
echo "  Running this file again replaces the installed copy with the one in this kit, which is"
echo "  how an update is applied. Your models, settings and output are not touched."
echo "  If anything looks wrong later, 'Technical/verify-kit.sh' checks this kit is complete."
echo
echo "  Press Return to close."
read _ || true
