#!/bin/sh
# SET UP THE SHOPS CONTROLLER
#
# Double-click this file on the always-on Mac mini that runs the Shops group (owner, 2026-10-03). That
# Mac is a server for other apps too (the music player, Media Studio KH), so **its own generation is
# paused**: it hands work to the shop Macs and makes nothing itself. This is the owner's ordinary Pause
# (the Gateway's Fleet page shows "Paused" and can Resume it any time) - set here before the app starts.
#
# Then 'Update this Mac.command' installs Studio Hub and Yam Display, copies only the workstation
# models (~15 GB, in case its generation is ever resumed) and starts the services.
#
# Safe to run again. The `STUDIOHUB_*` names are for tests; nobody sets them in real use.
set -u
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
KIT="$(dirname -- "$HERE")"
HUB_DATA="${STUDIOHUB_HUB_DATA:-$HOME/Library/Application Support/StudioHub Standalone/hub}"
UPDATE="${STUDIOHUB_UPDATE_CMD:-$KIT/Update this Mac.command}"
FLAGS="$HUB_DATA/machine_flags.json"

clear
echo "============================================================"
echo "  Studio Hub - setting up the Shops controller"
echo "============================================================"
echo

if [ ! -f "$UPDATE" ]; then
  echo "  I cannot find 'Update this Mac.command' in this kit (looked at $UPDATE)."
  echo "  Copy the whole kit folder to this Mac, then run this again."
  echo
  echo "  Press Return to close."
  read _ || true
  exit 1
fi

mkdir -p "$(dirname -- "$HUB_DATA")" && mkdir -m 700 -p "$HUB_DATA" || {
  echo "  Could not create $HUB_DATA"; read _ || true; exit 1; }
# The scheduler's own per-machine switch (`registry.set_machine_enabled`), the one Pause writes.
# `plutil` edits JSON in place and keeps every other Mac's and Studio's flag; no interpreter needed.
if [ ! -f "$FLAGS" ]; then
  printf '{\n  "local": {\n    "enabled": false\n  }\n}\n' > "$FLAGS"
else
  /usr/bin/plutil -replace local.enabled -bool NO "$FLAGS" 2>/dev/null \
    || /usr/bin/plutil -replace local -json '{"enabled":false}' "$FLAGS"
fi
if [ "$(/usr/bin/plutil -extract local.enabled raw "$FLAGS" 2>/dev/null)" = "false" ]; then
  echo "  This Mac's own generation: PAUSED (it hands work to the shop Macs)."
else
  echo "  Could not pause this Mac's own generation - pause it from the Gateway's Fleet page instead."
fi
# A running Hub holds the flags it read at start; the pause above reaches it when it restarts.
if ${STUDIOHUB_PGREP:-/usr/bin/pgrep -x StudioHub} >/dev/null 2>&1; then
  echo "  Studio Hub is running: the pause takes effect when it restarts - or press Pause on this Mac"
  echo "  in the Gateway's Fleet page now."
fi
echo

STUDIOHUB_WORKSTATION=1 /bin/sh "$UPDATE"
STATUS=$?

echo
echo "============================================================"
echo "  Last step, once: make it the Shops controller"
echo "============================================================"
echo
echo "  1. Studio Hub > Connections: set the role to Controller, turn on this Mac's Tailscale address,"
echo "     restart the services."
echo "  2. Copy Fleet Token - each shop Mac joins with this address and that token."
echo "  3. Add this controller to the Gateway as the Shops location."
echo
echo "  Press Return to close."
read _ || true
exit $STATUS
