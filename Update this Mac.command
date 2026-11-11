#!/bin/sh
# UPDATE THIS MAC
#
# Double-click this file. It brings this Mac up to date with this kit: the app if it is not already
# the same build, then the models.
#
# It does what '1 - Install the app' and '2 - Copy the models' do, one after the other, without
# asking you to press Return in between. Both of those files still work on their own; this is the
# one to use when you just want the Mac current.
#
# **The app is only replaced when it is actually different.** Running the two steps by hand always
# copied the whole app — quit it, copy ~5 GB, delete the old one, move the new one in — even when
# the Mac already had that exact build. On a fleet that is most of the work for none of the result.
# This compares the two builds first and skips the copy when they match.
#
# **Why the two steps cannot run at the same time.** '1' replaces /Applications/Hub Studio KH.app, and
# '2' runs its helper with the Python from inside that same app. Started together, '2' would either
# find no app at all — and fall back to the copy on this drive, which fails if the drive is
# unplugged — or keep running from files '1' has just deleted. So this file runs them in order and
# waits for each. The waiting is the feature.
#
# It is safe to run again: that is how an update is applied. Models, settings and your output are
# never touched.

set -u
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
KIT="$(dirname -- "$HERE")"
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
echo "  Hub Studio KH - updating this Mac"
echo "============================================================"
echo

# The app in this kit, in either place a launcher can sit.
KIT_APP=""
for spot in "$HERE/$NAME" "$KIT/$NAME"; do
  [ -d "$spot" ] && { KIT_APP="$spot"; break; }
done
if [ -z "$KIT_APP" ]; then
  echo "  I cannot find the app next to this file."
  echo "  Expected it at:  $HERE/$NAME"
  echo
  echo "  Copy the whole kit folder to this Mac, then run this again."
  echo
  echo "  Press Return to close."
  read _ || true
  exit 1
fi

# The app already on this Mac, looked for exactly where the model copy looks — the two must agree
# about which bundle is "the app", or this would compare one build and install another.
INSTALLED=""
# (`STUDIOHUB_INSTALL_TARGET` is the test stand-in for Applications; nobody sets it in real use.)
for app in "${STUDIOHUB_INSTALL_TARGET:-/Applications}/$NAME" "${STUDIOHUB_INSTALL_TARGET:-$HOME/Applications}/$NAME"; do
  [ -d "$app" ] && { INSTALLED="$app"; break; }
done

# Version and commit, read from each bundle's own release manifest. `plutil` reads JSON natively,
# so this needs no interpreter — which matters, because the interpreter this would otherwise use is
# inside the app being replaced.
identity() {
  manifest="$1/Contents/Resources/release-manifest.json"
  [ -f "$manifest" ] || { echo ""; return; }
  version=$(/usr/bin/plutil -extract version raw "$manifest" 2>/dev/null || echo "")
  commit=$(/usr/bin/plutil -extract implementation_commit raw "$manifest" 2>/dev/null || echo "")
  [ -n "$version" ] && [ -n "$commit" ] && printf '%s+%s' "$version" "$commit"
}

KIT_ID="$(identity "$KIT_APP")"
INSTALLED_ID=""
[ -n "$INSTALLED" ] && INSTALLED_ID="$(identity "$INSTALLED")"

echo "  This kit carries:   ${KIT_ID:-unknown}"
if [ -z "$INSTALLED" ]; then
  echo "  This Mac has:       nothing installed"
else
  echo "  This Mac has:       ${INSTALLED_ID:-unreadable build}"
fi
echo

if [ -n "$KIT_ID" ] && [ "$KIT_ID" = "$INSTALLED_ID" ]; then
  echo "  The app is already this exact build, so it is not being copied."
  echo "  (That is the slow part — about 5 GB — and there is nothing to gain from it.)"
  echo
  echo "  Continuing with the models."
  SKIP_APP=1
else
  if [ -z "$INSTALLED" ]; then
    echo "  Installing the app."
  else
    echo "  The app on this Mac is a different build, so it is being replaced."
  fi
  SKIP_APP=0
fi

if [ "$SKIP_APP" = "0" ]; then
  # `STUDIOHUB_UNATTENDED` skips this file's two stops: the Applications question (it takes the
  # default, this Mac's /Applications) and the closing "Press Return". The administrator password
  # prompt is macOS's own and still appears when /Applications needs it — that one cannot and
  # should not be skipped.
  STUDIOHUB_UNATTENDED=1 STUDIOHUB_YAMDISPLAY_BY_CALLER=1 /bin/sh "$HERE/1 - Install the app.command" || {
    echo
    echo "  Installing the app did not finish, so the models were left alone."
    echo "  Run this file again."
    echo
    echo "  Press Return to close."
    read _ || true
    exit 1
  }
fi

# Yam Display, whether or not the app was replaced: a Mac whose app is current may still lack it. Its
# failure is reported at the end and never stops the models or the fleet-mode start.
echo
echo "============================================================"
echo "  Yam Display"
echo "============================================================"
echo
YD_STATUS=0
install_yam_display "${STUDIOHUB_INSTALL_TARGET:-}" || YD_STATUS=1

# The model copy takes its Python from the installed app, so it runs *after* the swap above, never
# beside it. `STUDIOHUB_ASSUME_YES` answers its "press Return to start" the same way the step above
# was answered — this file already said what it is doing.
echo
echo "============================================================"
echo "  Models"
echo "============================================================"
echo
STUDIOHUB_ASSUME_YES=1 /bin/sh "$HERE/2 - Copy the models.command"
STATUS=$?

# Last, so the services start on a finished library. Done whether the app was replaced or already
# current: a Mac whose app is current may still have "Start services on startup" off.
echo
echo "============================================================"
echo "  Starting Hub Studio KH in fleet mode"
echo "============================================================"
echo
FLEET_TARGET="${INSTALLED:-/Applications/$NAME}"
for app in "/Applications/$NAME" "$HOME/Applications/$NAME"; do
  [ -d "$app" ] && { FLEET_TARGET="$app"; break; }
done
FLEET_STATUS=0
fleet_mode_open "$FLEET_TARGET" || FLEET_STATUS=1

echo
if [ "$YD_STATUS" != "0" ]; then
  echo "  Yam Display was NOT installed (see above). Run 'Extras/Install Yam Display.command' to try again;"
  echo "  everything else is unaffected."
  echo
fi
if [ "$STATUS" = "0" ] && [ "$FLEET_STATUS" = "0" ]; then
  echo "  This Mac is up to date."
elif [ "$STATUS" = "0" ]; then
  echo "  The app and models are up to date, but Hub Studio KH was not opened in fleet mode (see above)."
else
  echo "  The models step reported a problem above. Running this file again is safe and"
  echo "  picks up where it left off."
fi
echo
echo "  Press Return to close."
read _ || true
