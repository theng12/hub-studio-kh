#!/bin/sh
# 2 - COPY THE MODELS
#
# **This file offers the presets (owner decision 2026-10-30).** Hub Studio KH never reads a mounted
# drive by itself, so this command is the SSD path: it asks which of the four presets to copy
# (Essentials 8 GB, Creator 16 GB, Creator Plus 24 GB, Studio Pro 32 GB+) and copies only what is
# missing. A Mac that is already set up keeps every model it has.
#
# A Mac with no internet and no other Mac on the network to copy from can run
# "2 - Copy the models (everything).command" beside this one, which copies every model the Mac can
# run - this same file with STUDIOHUB_KIT_COPY=1.
#
# It is safe to run again, and running it again is how you fix a model that reads as "not
# installed": anything missing, or present at the wrong size, is copied over. Models already
# correct are left alone, so a second run takes seconds rather than minutes. A voice already on
# this Mac is never overwritten.
#
# It also moves out a model this kit no longer offers: if the app's own record still lists it and
# no model in this kit ships it, the folder is moved into "_trash" inside ~/StudioHubModels - not
# deleted - and the app's record is updated to match. Emptying that folder when you are happy
# nothing in it is wanted is what returns the space, and that is your call. A model the kit does
# ship is never removed, even when this Mac has too little memory to run it.
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
# **The installed app first, the copy in this kit last.** It used to be the other way round, so a
# Mac that had just run step 1 still executed the interpreter inside the *kit's* bundle — from a
# removable drive, which is slower, breaks if the drive is unplugged mid-copy, and puts a second
# copy of "Hub Studio KH" in front of macOS when the one that matters is the installed one. The kit's
# copy stays as the fallback for the one case it is for: this step on a Mac where the app is not
# installed yet.
#
# The bundle's name deliberately carries no version (`packaging/stage_voice.py` installs it as
# "Hub Studio KH.app"), so there is no pinned name to fall back to: a version-pinned candidate could
# only ever be dead weight, or worse, an older copy left beside a newer one.
for app in "/Applications/Hub Studio KH.app" "$HOME/Applications/Hub Studio KH.app" \
           "$HERE/Hub Studio KH.app" "$KIT/Hub Studio KH.app"; do
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
  echo "  I cannot find Hub Studio KH on this Mac."
  echo "  Double-click '1 - Install the app' first, then run this file again."
  echo
  echo "  Press Return to close."
  read _ || true
  exit 1
fi

# The helper picks the app bundle itself, and it must pick the same one this launcher took the
# interpreter from — otherwise the manifests it reads and the runtime that runs them come from two
# different bundles. Passing it explicitly is what makes the two answers one answer.
APP_ROOT="${PY%/Contents/Resources/runtimes/voice/bin/python3}"
STUDIOHUB_APP_ROOT="$APP_ROOT" exec "$PY" "$HELPER"
