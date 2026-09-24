#!/bin/sh
# 2 - COPY THE MODELS
#
# Double-click this file. It looks at how much memory this Mac has, copies the models that fit
# into a folder in your own home folder (~/StudioHubModels), leaves the ones that do not fit, and
# adds the voice clips the kit carries.
#
# It is safe to run again, and running it again is how you fix a model that reads as "not
# installed": anything missing, or present at the wrong size, is copied over. Models already
# correct are left alone, so a second run takes seconds rather than minutes. A voice already on
# this Mac is never overwritten.
#
# Nothing else is needed: the app finds this folder by itself the first time it opens, and the
# folder belongs to whichever account is signed in, so a different account name on another Mac
# needs nothing special.

set -u
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
KIT="$(dirname -- "$HERE")"

clear
# Use the app's own Python, so nothing has to be installed first.
# The helper lives in Technical/ when this launcher sits in the kit root, and beside this file
# when it does not. Look in both, and find the Python that ships inside the app.
HELPER=""
for spot in "$HERE/Technical/copy_models_for_this_mac.py" "$HERE/copy_models_for_this_mac.py"; do
  [ -f "$spot" ] && { HELPER="$spot"; break; }
done

PY=""
# The bundle's name deliberately carries no version (`packaging/stage_voice.py` installs it as
# "Studio Hub.app"), so there is no pinned name to fall back to: a version-pinned candidate could
# only ever be dead weight, or worse, an older copy left beside a newer one.
for app in "$HERE/Studio Hub.app" "$KIT/Studio Hub.app" \
           "/Applications/Studio Hub.app" "$HOME/Applications/Studio Hub.app"; do
  if [ -x "$app/Contents/Resources/runtimes/voice/bin/python3" ]; then
    PY="$app/Contents/Resources/runtimes/voice/bin/python3"; break
  fi
done

if [ -z "$HELPER" ]; then
  echo "  I cannot find the model-copy helper next to this file."
  echo
  echo "  Press Return to close."
  read _ || true
  exit 1
fi

if [ -z "$PY" ]; then
  echo "  I cannot find Studio Hub on this Mac."
  echo "  Double-click '1 - Install the app' first, then run this file again."
  echo
  echo "  Press Return to close."
  read _ || true
  exit 1
fi

exec "$PY" "$HELPER"
