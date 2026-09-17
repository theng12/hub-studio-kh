#!/bin/sh
# 2 - COPY THE MODELS
#
# Double-click this file. It looks at how much memory this Mac has, copies the models
# that fit into your Downloads folder, leaves the ones that do not, and adds the voice
# clips the kit carries. Running it again adds anything new and never overwrites a voice
# already on this Mac.

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
for app in "$HERE/Studio Hub.app" "$KIT/Studio Hub.app" \
           "/Applications/Studio Hub.app" "$HOME/Applications/Studio Hub.app" \
           "$HERE/Studio Hub 0.4.0.app" "$KIT/Studio Hub 0.4.0.app" \
           "/Applications/Studio Hub 0.4.0.app" "$HOME/Applications/Studio Hub 0.4.0.app"; do
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
