#!/bin/sh
# 2 - COPY THE LITE MODE MODELS
#
# Double-click this file instead of '2 - Copy the models' on a Mac that will run Lite Mode.
# This copies only the models Lite Mode uses (about 15 GB). Next: open Studio Hub > Settings >
# Lite Mode and tick it.
#
# A Lite Mode Mac is set up like any other Mac: '1 - Install the app', then a model copy (this
# one, or '2 - Copy the models' if the Mac should also keep the bigger models), then join its
# controller if it is a node. This file does NOT change the Mac's role and does NOT tick Lite Mode.
#
# It is '2 - Copy the models' with one switch, so everything that file does is true here too: it is
# safe to run again (anything missing or at the wrong size is copied; models already correct are
# left alone), it adds the voice clips the kit carries without overwriting a voice already here,
# and a model this kit no longer offers is moved to "_trash" inside ~/StudioHubModels, not deleted.
# Models that are not Lite Mode models are simply not copied; any already on this Mac stay.
set -u
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

COPY=""
for spot in "$HERE/2 - Copy the models.command" "$HERE/copy-models.command" \
            "$HERE/Technical/copy-models.command"; do
  [ -f "$spot" ] && { COPY="$spot"; break; }
done
if [ -z "$COPY" ]; then
  echo "  I cannot find '2 - Copy the models.command' next to this file."
  echo "  Copy the whole kit folder to this Mac, then run this again."
  echo
  echo "  Press Return to close."
  read _ || true
  exit 1
fi

# The one switch: the copy helper reads this and keeps to the list in components/workstation_models.py.
STUDIOHUB_WORKSTATION=1 /bin/sh "$COPY"
STATUS=$?

echo
echo "============================================================"
if [ "$STATUS" -eq 0 ]; then
  echo "  This copied only the models Lite Mode uses (about 15 GB)."
  echo "  Next: open Studio Hub > Settings > Lite Mode and tick it."
else
  echo "  Something did not finish - read the lines above, then run this file again."
fi
echo "============================================================"
echo
exit $STATUS
