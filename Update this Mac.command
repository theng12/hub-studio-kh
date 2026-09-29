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
# **Why the two steps cannot run at the same time.** '1' replaces /Applications/Studio Hub.app, and
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
NAME="Studio Hub.app"

clear
echo "============================================================"
echo "  Studio Hub - updating this Mac"
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
for app in "/Applications/$NAME" "$HOME/Applications/$NAME"; do
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
  STUDIOHUB_UNATTENDED=1 /bin/sh "$HERE/1 - Install the app.command" || {
    echo
    echo "  Installing the app did not finish, so the models were left alone."
    echo "  Run this file again."
    echo
    echo "  Press Return to close."
    read _ || true
    exit 1
  }
fi

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

echo
if [ "$STATUS" = "0" ]; then
  echo "  This Mac is up to date."
else
  echo "  The models step reported a problem above. Running this file again is safe and"
  echo "  picks up where it left off."
fi
echo
echo "  Press Return to close."
read _ || true
