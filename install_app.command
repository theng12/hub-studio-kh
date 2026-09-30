#!/bin/sh
# 1 - INSTALL THE APP
#
# Double-click this file. It copies Studio Hub onto this Mac.
# Nothing to type, nothing to configure.

set -u
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
KIT="$(dirname -- "$HERE")"
# This launcher sits either in the kit root or in its Technical folder; the app is beside one of
# them. The bundle's name carries no version, so there is no version-pinned name to look for.
APP=""
for spot in "$HERE/Studio Hub.app" "$KIT/Studio Hub.app"; do
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
# Applications needs an administrator password. If that is a problem, install for this user only,
# which needs no password and works just as well.
# `STUDIOHUB_INSTALL_TARGET` is for testing this script against a scratch folder; nobody sets it in
# real use. Overriding it also means the running app is not asked to quit, because the copy that is
# running is not the one being replaced.
TARGET="${STUDIOHUB_INSTALL_TARGET:-/Applications}"
NEEDS_ADMIN=1
if [ -w "$TARGET" ]; then NEEDS_ADMIN=0; fi

echo "  This will replace any Studio Hub already installed."
echo
# **Unattended when another script is driving.** Two of this file's three stops are for a person
# who double-clicked it: which Applications folder to use, and reading the result before the window
# closes. `Update this Mac.command` runs this as the first of two steps and has already said what
# it is doing, so both stops would be a hang with nobody at the keyboard. The answer it would have
# given is the default — this Mac's /Applications — so that is what it gets.
if [ "${STUDIOHUB_UNATTENDED:-}" = "1" ]; then
  echo "  Installing into Applications."
else
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
fi

echo
echo "  Updating... (a few seconds when this Mac already has an older Studio Hub)"
# A running copy holds its own files open, and replacing one underneath itself is how an install
# half-lands - the old process keeps serving from deleted files while the folder on disk changes.
if [ -z "${STUDIOHUB_INSTALL_TARGET:-}" ]; then
  osascript -e 'quit app "Studio Hub"' >/dev/null 2>&1 || true
  osascript -e 'quit app "StudioHub"' >/dev/null 2>&1 || true
  sleep 2
fi
# **The new app is built beside the old one and swapped in only when it is complete.** Two things
# this used to get wrong: it deleted the installed app before the new one was in place (so a failed
# move left the Mac with no app at all), and it copied all ~5 GB even when a release differs from
# the last by a handful of files. Now the installed app is cloned (APFS: seconds, almost no extra
# disk), what differs is brought across with rsync, and the old app is kept until the new one has
# landed and been checked - and put back if it did not. Without an installed app, or off APFS, it
# falls back to a plain copy. The steps are one script so the administrator prompt shows once.
SWAP="$(mktemp -t studiohub-install)"
trap 'rm -f "$SWAP"' EXIT
cat > "$SWAP" <<'STEPS'
#!/bin/sh
# usage: <kit app> <target folder> <app name>
APP="$1"; TARGET="$2"; NAME="$3"
STAGING="$TARGET/.studiohub-incoming"
OLD="$TARGET/.studiohub-previous"
LIVE="$TARGET/$NAME"
RUNTIME="Contents/Resources/runtimes/voice/bin/python3"
# The app ships read-only runtimes, so a plain `rm -rf` fails with "Permission denied".
gone() { if [ -e "$1" ]; then chmod -R u+w "$1" 2>/dev/null; rm -rf "$1"; fi; return 0; }
gone "$STAGING"; gone "$OLD"        # leftovers of an interrupted run
if [ -d "$LIVE" ] && /bin/cp -c -R "$LIVE" "$STAGING" 2>/dev/null; then
  # The clone is read-only like the original; rsync has to write into it.
  if ! { chmod -R u+w "$STAGING" && /usr/bin/rsync -a --delete "$APP/" "$STAGING/"; }; then
    gone "$STAGING"
    /bin/cp -Rp "$APP" "$STAGING" || { gone "$STAGING"; exit 1; }
  fi
else
  gone "$STAGING"
  /bin/cp -Rp "$APP" "$STAGING" || { gone "$STAGING"; exit 1; }
fi
[ -x "$STAGING/$RUNTIME" ] || { gone "$STAGING"; exit 2; }
if [ -e "$LIVE" ]; then mv "$LIVE" "$OLD" || { gone "$STAGING"; exit 3; }; fi
if ! mv "$STAGING" "$LIVE" || [ ! -x "$LIVE/$RUNTIME" ]; then
  gone "$LIVE"
  if [ -e "$OLD" ]; then mv "$OLD" "$LIVE"; fi
  gone "$STAGING"
  exit 4
fi
gone "$OLD"
exit 0
STEPS
if [ "$NEEDS_ADMIN" = "1" ]; then
  # One administrator prompt, the normal macOS way.
  /usr/bin/osascript -e "do shell script \"/bin/sh '$SWAP' '$APP' '$TARGET' '$NAME'\" with administrator privileges" >/dev/null 2>&1
else
  /bin/sh "$SWAP" "$APP" "$TARGET" "$NAME"
fi
if [ "$?" != "0" ]; then
  echo
  echo "  That did not work, and the app that was installed before is still there: the new copy is"
  echo "  made beside it and only swapped in once it is complete, and swapped back if it is not."
  echo
  echo "  Check free space and try again (enter your password if asked), or run this file again"
  echo "  and answer 'n' to install into your own Applications folder."
  echo
  if [ "${STUDIOHUB_UNATTENDED:-}" != "1" ]; then
    echo "  Press Return to close."
    read _ || true
  fi
  exit 1
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
# The closing pause is for a person reading the result. When `Update this Mac.command` is driving,
# the next step follows immediately and Return never comes.
if [ "${STUDIOHUB_UNATTENDED:-}" != "1" ]; then
  echo "  Press Return to close."
  read _ || true
fi
