#!/bin/sh
# SET UP A SHOP MAC
#
# Double-click this file on a shop's cashier Mac (owner, 2026-10-03, "Shops"). It makes the Mac a
# Studio Hub node in **Lite Mode**: it lends its idle time to the fleet and never its user's.
#
#   1. Lite Mode is switched on, with its defaults: every job at background priority, only the
#      8 GB-class models, quiet (no pop-ups). "Only when idle", "Step aside when someone uses it",
#      quiet hours and the rest are set later in Studio Hub's Settings on this Mac, or from the
#      Gateway's Fleet page.
#   2. 'Update this Mac.command' then installs Studio Hub and Yam Display and copies ONLY the
#      Lite Mode models (about 15 GB: FLUX.2 Klein 4B 4-bit, Z-Image-Turbo 4-bit, Qwen3-TTS 0.6B
#      4-bit, VoxCPM2 4-bit, Whisper large-v3 turbo, Stable Audio 3 Medium), and starts the services.
#   3. You join it to the Shops controller once, in the app (the last screen says how).
#
# Safe to run again: that is how a Shop Mac is updated. A Mac whose Lite Mode settings were already
# changed keeps them.
#
# The `STUDIOHUB_*` names are for tests; nobody sets them in real use.
set -u
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
KIT="$(dirname -- "$HERE")"
HUB_DATA="${STUDIOHUB_HUB_DATA:-$HOME/Library/Application Support/StudioHub Standalone/hub}"
UPDATE="${STUDIOHUB_UPDATE_CMD:-$KIT/Update this Mac.command}"

clear
echo "============================================================"
echo "  Studio Hub - setting up a Shop Mac"
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

# The Hub refuses a state folder anyone else can read, so it is made owner-only, as the Hub makes it.
mkdir -p "$(dirname -- "$HUB_DATA")" && mkdir -m 700 -p "$HUB_DATA" || {
  echo "  Could not create $HUB_DATA"; read _ || true; exit 1; }
if [ -f "$HUB_DATA/workstation.json" ]; then
  echo "  Lite Mode is already set up on this Mac - its settings are kept."
else
  printf '{\n  "enabled": true\n}\n' > "$HUB_DATA/workstation.json" && chmod 600 "$HUB_DATA/workstation.json"
  echo "  Lite Mode: ON (background priority, 8 GB-class models only, no pop-ups)."
fi
echo

STUDIOHUB_WORKSTATION=1 /bin/sh "$UPDATE"
STATUS=$?

echo
echo "============================================================"
echo "  Last step, once: join the Shops controller"
echo "============================================================"
echo
echo "  1. Open Studio Hub > Connections, turn on this Mac's Tailscale address, restart the services."
echo "  2. Join: the Shops controller's address and its Fleet token."
echo "  After that everything else - Only when idle, Step aside, quiet hours, hiding the menu-bar"
echo "  icon - is set in Studio Hub > Settings > Lite Mode on this Mac, or from the Gateway's Fleet page."
echo
echo "  Press Return to close."
read _ || true
exit $STATUS
