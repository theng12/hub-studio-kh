#!/bin/bash
# Put the kit's Extras folder in order: the Yam Display installer and the standalone command that
# installs it. Called by the per-release prepare-kit script, or by hand:
#
#   bash packaging/kit-extras/prepare-kit-extras.sh "<kit folder>" "<trash folder>"
#
# <kit folder> is ".../Video Generation Kit/Studio Hub KH"; <trash folder> is that volume's `_trash`.
# The installer comes from KIT_ASSETS (default ~/Developer/_orchestra/kit-assets) and the command
# from this folder; both are copied and their sha256 is checked against the source. An older
# YamDisplay*.dmg already in Extras is moved to the trash folder, never deleted. Anything else in
# Extras is the owner's and is left alone: only the two names below, and the retired commands named
# at the end, are managed here (the kit's Technical prune never looks in Extras).
# Exit status: 0 prepared, 1 not (and it says why).
set -u
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
KIT="${1:?usage: prepare-kit-extras.sh <kit folder> <trash folder>}"
TRASH="${2:?usage: prepare-kit-extras.sh <kit folder> <trash folder>}"
ASSETS="${KIT_ASSETS:-$HOME/Developer/_orchestra/kit-assets}"
COMMAND="Install Yam Display.command"

[ -d "$KIT" ] || { echo "   Extras NOT prepared: no kit at $KIT"; exit 1; }
DMG=""
for f in "$ASSETS"/YamDisplay*.dmg; do
  [ -f "$f" ] || continue
  if [ -z "$DMG" ] || [ "$(printf '%s\n%s\n' "$DMG" "$f" | sort -V | tail -1)" = "$f" ]; then DMG="$f"; fi
done
[ -n "$DMG" ] || { echo "   Extras NOT prepared: no YamDisplay*.dmg in $ASSETS"; exit 1; }
[ -f "$HERE/$COMMAND" ] || { echo "   Extras NOT prepared: no '$COMMAND' beside this script"; exit 1; }

DMGNAME="$(basename "$DMG")"
mkdir -p "$KIT/Extras" "$TRASH/Extras-retired" || exit 1
for old in "$KIT/Extras"/YamDisplay*.dmg; do
  [ -e "$old" ] && [ "$(basename "$old")" != "$DMGNAME" ] || continue
  dest="$TRASH/Extras-retired/$(basename "$old")"
  [ -e "$dest" ] && dest="$dest-$(date +%Y%m%d%H%M%S)"
  mv "$old" "$dest" && echo "   _trash: Extras/$(basename "$old")"
done

status=0
copy_checked() { # source name
  cp -f "$1" "$KIT/Extras/$2" || { echo "   Extras: copying $2 FAILED"; status=1; return; }
  want=$(shasum -a 256 "$1" | cut -d' ' -f1)
  got=$(shasum -a 256 "$KIT/Extras/$2" | cut -d' ' -f1)
  if [ "$want" = "$got" ]; then echo "   Extras/$2 sha256 $want"; else echo "   Extras/$2 sha256 MISMATCH (source $want, kit $got)"; status=1; fi
}
copy_checked "$DMG" "$DMGNAME"
copy_checked "$HERE/$COMMAND" "$COMMAND"
chmod 644 "$KIT/Extras/$DMGNAME"
chmod 755 "$KIT/Extras/$COMMAND"
# Retired commands (owner): a drive prepared earlier still has them; they go to the volume's trash,
# never deleted.
#  - 2026-10-04 `Set Up the Shops Controller`: the Shops controller is a regular controller that also
#    generates - set it up like any Mac, then tick Lite Mode.
#  - 2026-10-04 `Set Up a Shop Mac`: a Lite Mode Mac is set up like any Mac, then Lite Mode is ticked in
#    Studio Hub > Settings (and takes the Essentials 8 GB preset like any Mac; the Lite-only copy
#    launcher was retired 2026-10-30 - `prepare-kit.sh` moves a drive's copy to its `_trash`).
for name in "Set Up the Shops Controller.command" "Set Up a Shop Mac.command"; do
  RETIRED="$KIT/Extras/$name"
  if [ -e "$RETIRED" ]; then
    mv "$RETIRED" "$TRASH/Extras-retired/$name-$(date +%Y%m%d%H%M%S)" \
      && echo "   _trash: Extras/$name (retired)"
  fi
done
exit $status
