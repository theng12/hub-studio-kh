#!/bin/sh
# 2 - COPY THE MODELS (EVERYTHING)
#
# The older behaviour of "2 - Copy the models": copies every model this Mac's memory can run, and
# moves out a model the kit no longer offers. Use it on a Mac that has no internet and no other Mac
# on the network to copy a model from - everywhere else, Studio Hub's "Set up this Mac" screen does
# the choosing, with the presets, the free space and the cost shown first.
#
# It is the same helper with one switch, so everything that file does is true here too: it is safe
# to run again, models already correct are left alone, an updated model replaces the old one in
# place, and anything it moves goes to "_trash" inside the library, never to nothing.
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

STUDIOHUB_KIT_COPY=1 /bin/sh "$COPY"
STATUS=$?

echo
echo "============================================================"
if [ "$STATUS" -eq 0 ]; then
  echo "  Done. Every model this Mac's memory can run is in place."
else
  echo "  Something did not finish - read the lines above, then run this file again."
fi
echo "============================================================"
echo
exit $STATUS
