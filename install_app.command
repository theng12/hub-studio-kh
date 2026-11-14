#!/bin/sh
# 1 - INSTALL THE APP
#
# Double-click this file. It copies Hub Studio KH onto this Mac.
# Nothing to type, nothing to configure.

set -u
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
KIT="$(dirname -- "$HERE")"
# This launcher sits either in the kit root or in its Technical folder; the app is beside one of
# them. The bundle's name carries no version, so there is no version-pinned name to look for.
APP=""
for spot in "$HERE/Hub Studio KH.app" "$KIT/Hub Studio KH.app"; do
  [ -d "$spot" ] && { APP="$spot"; break; }
done
APP="${APP:-$HERE/Hub Studio KH.app}"
# The installed name carries no version, so a new release replaces the old app instead of
# leaving a second copy beside it.
NAME="Hub Studio KH.app"

# **Make this Mac serve after a restart (owner, 2026-10-02).** The app starts at login by default but
# its services do not: "Start services on startup" is off unless the app was opened once with
# `--fleet-mode`, which no kit step did - so every Mac set up from the drive stayed silent to its
# controller after a reboot. Opening the app this way writes the setting down (and starts the services
# now). A running app ignores arguments, so it is closed first - gracefully, never killed: if it is busy
# or waiting for an answer it is left alone and the Mac is told to run this again.
#
# The `STUDIOHUB_*` names are for testing this against a scratch folder with stand-in commands; nobody
# sets them in real use. A test that points the install at a scratch folder never opens the real app.
fleet_mode_open() {
  app="$1"
  if [ -n "${STUDIOHUB_INSTALL_TARGET:-}" ] && [ -z "${STUDIOHUB_OPEN_CMD:-}" ]; then return 0; fi
  opener="${STUDIOHUB_OPEN_CMD:-/usr/bin/open}"
  prefs="${STUDIOHUB_PREFS:-$HOME/Library/Application Support/StudioHub Standalone/app-preferences.json}"
  is_running() { set -- ${STUDIOHUB_PGREP:-/usr/bin/pgrep -x StudioHub}; "$@" >/dev/null 2>&1; }
  already=$(/usr/bin/plutil -extract startServicesAtLogin raw "$prefs" 2>/dev/null || echo "")
  if is_running; then
    if [ "$already" = "true" ]; then
      echo "  Hub Studio KH is running and already starts its services after a restart."
      return 0
    fi
    echo "  Closing Hub Studio KH so it can be reopened in fleet mode..."
    set -- ${STUDIOHUB_OSASCRIPT:-/usr/bin/osascript}
    "$@" -e 'with timeout of 20 seconds' -e 'quit app "Hub Studio KH"' -e 'end timeout' >/dev/null 2>&1 || true
    waited=0
    while is_running && [ "$waited" -lt "${STUDIOHUB_QUIT_WAIT:-40}" ]; do sleep 1; waited=$((waited + 1)); done
    if is_running; then
      echo
      echo "  Hub Studio KH did not close - it may be busy with a job or waiting for an answer, so it was"
      echo "  left alone. Close it yourself, then run this file again so this Mac is set to start its"
      echo "  services after a restart."
      return 1
    fi
  fi
  if /usr/bin/xattr -p com.apple.quarantine "$app" >/dev/null 2>&1; then
    echo
    echo "  macOS has not opened this app before: if it asks, right-click Hub Studio KH in Applications and"
    echo "  choose Open (once), then run this file again."
  fi
  echo "  Opening Hub Studio KH in fleet mode: it starts its services now, at every login and after a restart."
  if "$opener" -a "$app" --args --fleet-mode; then
    return 0
  fi
  echo
  echo "  macOS would not open Hub Studio KH. Right-click it in Applications and choose Open (once), then run"
  echo "  this file again so this Mac is set to start its services after a restart."
  return 1
}

# **Yam Display (owner, 2026-10-03).** Every fleet Mac needs it - it is the virtual display that lets a
# headless Mac mini render and be screen-shared. Its installer rides in the kit's Extras folder and
# `Extras/Install Yam Display.command` does the work: install when missing or older, never downgrade,
# one line and move on when the installer is not in the kit. A Yam Display problem never stops or
# fails the Hub Studio KH install: this returns 1 so the caller can say so plainly, and nothing more.
# The `STUDIOHUB_*` names are for tests; nobody sets them in real use.
install_yam_display() {
  yd_cmd=""
  for yd_dir in "${STUDIOHUB_EXTRAS_DIR:-$HERE/Extras}" "$KIT/Extras"; do
    [ -f "$yd_dir/Install Yam Display.command" ] && { yd_cmd="$yd_dir/Install Yam Display.command"; break; }
  done
  if [ -z "$yd_cmd" ]; then
    echo "  Yam Display: not in this kit (no Extras folder beside this file) - skipped."
    return 0
  fi
  STUDIOHUB_UNATTENDED=1 STUDIOHUB_YAMDISPLAY_TARGET="$1" /bin/sh "$yd_cmd"
}

clear
echo "============================================================"
echo "  Hub Studio KH - installing the app"
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

echo "  This will replace any Hub Studio KH already installed."
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
echo "  Updating... (a few seconds when this Mac already has an older Hub Studio KH)"
# A running copy holds its own files open, and replacing one underneath itself is how an install
# half-lands - the old process keeps serving from deleted files while the folder on disk changes.
if [ -z "${STUDIOHUB_INSTALL_TARGET:-}" ]; then
  osascript -e 'quit app "Hub Studio KH"' >/dev/null 2>&1 || true
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
archive_swap_script() {
  [ -e "$SWAP" ] || return 0
  swap_trash="$(dirname -- "$APP")/_trash"
  mkdir -p "$swap_trash" 2>/dev/null || return 0
  stamp=$(/bin/date -u +%Y%m%d-%H%M%S)
  destination="$swap_trash/install-script-$stamp-$$"
  suffix=0
  while [ -e "$destination" ] || [ -L "$destination" ]; do
    suffix=$((suffix + 1))
    destination="$swap_trash/install-script-$stamp-$$-$suffix"
  done
  mv "$SWAP" "$destination" 2>/dev/null || true
}
trap archive_swap_script EXIT
cat > "$SWAP" <<'STEPS'
#!/bin/sh
# usage: <kit app> <target folder> <app name>
APP="$1"; TARGET="$2"; NAME="$3"
STAGING="$TARGET/.studiohub-incoming"
OLD="$TARGET/.studiohub-previous"
LIVE="$TARGET/$NAME"
TRASH="$TARGET/_trash"
RUNTIME="Contents/Resources/runtimes/voice/bin/python3"
present() { [ -e "$1" ] || [ -L "$1" ]; }
archive() {
  source="$1"; label="$2"; original_mode=""
  present "$source" || return 0
  mkdir -p "$TRASH" || return 1
  stamp=$(/bin/date -u +%Y%m%d-%H%M%S)
  destination="$TRASH/$label-$stamp-$$"
  suffix=0
  while present "$destination"; do
    suffix=$((suffix + 1))
    destination="$TRASH/$label-$stamp-$$-$suffix"
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
# Recover a previous run that stopped after moving the good app aside. Other leftovers are evidence,
# so move them into a new archive entry without changing anything already in _trash.
if ! present "$LIVE" && present "$OLD"; then mv "$OLD" "$LIVE" || exit 10; fi
archive "$STAGING" interrupted-incoming || exit 11
archive "$OLD" interrupted-previous || exit 12
if [ -d "$LIVE" ] && /bin/cp -c -R "$LIVE" "$STAGING" 2>/dev/null; then
  # The clone is read-only like the original; rsync has to write into it.
  if ! { chmod -R u+w "$STAGING" && /usr/bin/rsync -a --delete "$APP/" "$STAGING/"; }; then
    archive "$STAGING" clone-update-failed || exit 13
    /bin/cp -Rp "$APP" "$STAGING" || { archive "$STAGING" incomplete-copy; exit 1; }
  fi
else
  archive "$STAGING" clone-copy-failed || exit 14
  /bin/cp -Rp "$APP" "$STAGING" || { archive "$STAGING" incomplete-copy; exit 1; }
fi
[ -x "$STAGING/$RUNTIME" ] || { archive "$STAGING" incomplete-copy; exit 2; }
if present "$LIVE"; then mv "$LIVE" "$OLD" || { archive "$STAGING" unpublished-copy; exit 3; }; fi
if ! mv "$STAGING" "$LIVE" || [ ! -x "$LIVE/$RUNTIME" ]; then
  archive "$LIVE" failed-install || exit 15
  if present "$OLD"; then mv "$OLD" "$LIVE" || exit 16; fi
  archive "$STAGING" failed-install || exit 17
  exit 4
fi
archive "$OLD" previous-app || exit 18
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
echo "  Installed Hub Studio KH $VERSION"
echo "  Installed to:  $TARGET/$NAME"
echo
# Yam Display next, into the same Applications folder. `Update this Mac.command` does this step itself
# (it also has to run when the app is already current), so it says so and this one stands aside.
if [ "${STUDIOHUB_YAMDISPLAY_BY_CALLER:-}" != "1" ]; then
  install_yam_display "${STUDIOHUB_YAMDISPLAY_TARGET:-$TARGET}" || {
    echo
    echo "  Hub Studio KH is installed. Yam Display was not - see above."
  }
  echo
fi
# When `Update this Mac.command` is driving, it opens the app itself once the models are copied: opening
# it now would start the services while the models are still arriving.
if [ "${STUDIOHUB_UNATTENDED:-}" != "1" ]; then
  fleet_mode_open "$TARGET/$NAME" || true
  echo
fi
echo "  NEXT:  double-click '2 - Copy the models.command' to bring over the models."
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
