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
rm -rf "$TARGET/$NAME" 2>/dev/null
if [ "$NEEDS_ADMIN" = "1" ]; then
  # One administrator prompt, the normal macOS way.
  if ! osascript -e "do shell script \"/bin/cp -Rp '$APP' '$TARGET/'\" with administrator privileges" 2>/dev/null; then
    echo
    echo "  That did not work. Try again and enter your password, or run this file again and"
    echo "  answer 'n' to install into your own Applications folder."
    echo
    echo "  Press Return to close."
    read _ || true
    exit 1
  fi
else
  if ! /bin/cp -Rp "$APP" "$TARGET/"; then
    echo "  The copy failed."
    echo
    echo "  Press Return to close."
    read _ || true
    exit 1
  fi
fi

# macOS blocks an unsigned app the first time. Clear the flag so a double-click just works.
xattr -cr "$TARGET/$NAME" 2>/dev/null || true

echo
echo "  Installed to:  $TARGET/$NAME"
echo
echo "  NEXT:  open it once (right-click it and choose Open), then double-click"
echo "         '2 - Copy the models.command' to bring over the models."
echo
echo "  Press Return to close."
read _ || true
