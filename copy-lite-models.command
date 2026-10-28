#!/bin/sh
# 2 - COPY THE LITE MODE MODELS - RETIRED, 2026-10-28
#
# This is no longer a separate install option. A Lite Mode Mac (a shop's cashier Mac, 8 GB) is set
# up like any other Mac: run "1 - Install the app", then copy models - and it takes the
# **Essentials 8 GB** preset from Studio Hub's "Set up this Mac" screen, which holds every model
# Lite Mode runs. Then open Settings > Lite Mode and tick it.
#
# What has NOT changed: Lite Mode still runs only its own short-lane models - the list in
# `components/workstation_models.py`. This file names no count on purpose: a number copied into
# prose goes stale the next time a lane is added. The Hub still refuses a job for any
# other model at a Lite Mode Mac's door with a plain reason. That list is a runtime allowlist now,
# not an install set.
#
# This file stays so a shortcut on a drive does not silently do nothing; it points at the one copy
# step. LITE MODE IS NOT TICKED HERE and the Mac's role is not changed.
set -u
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

echo "============================================================"
echo "  Lite Mode no longer has its own model set."
echo
echo "  Set this Mac up like any other: '1 - Install the app', then open Studio Hub and use"
echo "  'Set up this Mac' with the **Essentials 8 GB** preset - it holds every model Lite Mode"
echo "  uses. Then tick Lite Mode in Studio Hub > Settings."
echo
echo "  This file will now run '2 - Copy the models', which shows the drive's contents and"
echo "  points at that screen. Nothing is ticked and this Mac's role is not changed."
echo "============================================================"
echo

COPY=""
for spot in "$HERE/2 - Copy the models.command" "$HERE/copy-models.command" \
            "$HERE/Technical/copy-models.command"; do
  [ -f "$spot" ] && { COPY="$spot"; break; }
done
if [ -z "$COPY" ]; then
  echo "  I cannot find '2 - Copy the models.command' next to this file."
  echo
  echo "  Press Return to close."
  read _ || true
  exit 1
fi
/bin/sh "$COPY"
