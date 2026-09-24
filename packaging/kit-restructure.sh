#!/bin/bash
# Lay each SSD out as "Video Generation Kit/{GatewayKH,Story Studio,Studio Hub KH}".
#
# Safe by construction: every move is a rename inside one volume, so the model library is
# never copied, and nothing is overwritten - a destination that already exists is refused
# and reported. Duplicates go to the drive's own _trash (never deleted). The readmes come
# from packaging/kit-readmes/ so the layout is reproducible from this repository.
#
#   bash packaging/kit-restructure.sh            # every mounted kit drive
#   bash packaging/kit-restructure.sh NOVA       # only /Volumes/NOVA
#   DRY=1 bash packaging/kit-restructure.sh      # show what it would do
#
# With no arguments every mounted volume is examined and each one that is not a kit is named
# rather than passed over: the drive list used to be four hardcoded names, so a new drive was
# never visited and nothing said so. A volume counts as a kit when its root carries one of the
# kit's own folders or a Story Studio installer, not because of what the drive is called.
#
# Installers are matched by pattern (`Story Studio KH*.dmg`, `StudioHub-*.dmg`), because a
# version-pinned name means a new build silently stays where it is. The GatewayKH guides and
# SHA256SUMS are repointed at the sibling Studio Hub KH folder, because the spare StudioHub DMG
# inside GatewayKH/installers is a second copy of a kit that already ships in full.
set -u

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
SRC="$ROOT_DIR/packaging/kit-readmes"
DRY="${DRY:-0}"
STAMP="${STAMP:-$(date +%Y-%m-%d)}"
KIT_FOLDERS=("Video Generation Kit" "GatewayKH App" "Studio Hub App")

# Is this volume one of ours? Its contents say so; the drive's name does not.
is_kit_volume() { # root
  local root="$1" marker
  for marker in "${KIT_FOLDERS[@]}"; do
    [ -e "$root/$marker" ] && return 0
  done
  local candidate
  for candidate in "$root"/Story\ Studio\ KH*.dmg; do
    [ -e "$candidate" ] && return 0
  done
  return 1
}

DRIVES=("$@")
if [ "${#DRIVES[@]}" -eq 0 ]; then
  for volume in /Volumes/*; do
    [ -d "$volume" ] || continue
    [ -L "$volume" ] && continue            # the boot volume's own mount
    name="$(basename "$volume")"
    case "$name" in .*) continue ;; esac    # hidden system volumes
    if is_kit_volume "$volume"; then
      DRIVES+=("$name")
    else
      echo "===== $name"
      echo "  not a Studio Hub KH kit (no ${KIT_FOLDERS[*]} and no Story Studio DMG at its root), skipped"
    fi
  done
  if [ "${#DRIVES[@]}" -eq 0 ]; then
    echo "No kit volume is mounted (looked at every volume under /Volumes)."
  fi
fi

run() { if [ "$DRY" = "1" ]; then echo "  DRY: $*"; else "$@"; fi; }

move_into() { # src dst
  local src="$1" dst="$2"
  if [ ! -e "$src" ]; then echo "  skip (absent): $src"; return 0; fi
  if [ -e "$dst" ]; then echo "  REFUSE (exists): $dst"; return 0; fi
  echo "  move: $src"
  run mv "$src" "$dst"
}

for V in "${DRIVES[@]}"; do
  ROOT="/Volumes/$V"
  echo "===== $V"
  if [ ! -d "$ROOT" ]; then echo "  NOT MOUNTED, skipped"; continue; fi
  KIT="$ROOT/Video Generation Kit"
  TRASH="$ROOT/_trash"

  run mkdir -p "$KIT" "$KIT/Story Studio"

  move_into "$ROOT/GatewayKH App" "$KIT/GatewayKH"
  move_into "$ROOT/Studio Hub App" "$KIT/Studio Hub KH"
  # Whatever Story Studio installer this drive carries, by pattern: a build this script has never
  # seen is still placed, and a second one left beside it is moved too rather than left behind.
  for loose in "$ROOT"/Story\ Studio\ KH*.dmg; do
    [ -e "$loose" ] || continue
    move_into "$loose" "$KIT/Story Studio/$(basename "$loose")"
  done

  # Duplicates -> this drive's _trash, keeping the sub-structure.
  run mkdir -p "$TRASH/duplicate-kit-$STAMP"
  # A spare Studio Hub installer inside the gateway kit is a duplicate of the sibling Studio Hub
  # KH folder whatever version it is, so the pattern - not a pinned filename - decides.
  for duplicate in "$KIT/GatewayKH/installers"/StudioHub-*.dmg; do
    [ -e "$duplicate" ] || continue
    echo "  duplicate: $(basename "$duplicate") -> _trash/duplicate-kit-$STAMP/"
    run mv "$duplicate" "$TRASH/duplicate-kit-$STAMP/$(basename "$duplicate")"
  done
  # The kit's own README is superseded by packaging/kit-readmes/GatewayKH.md. Compare before
  # moving, so a second run cannot mistake our README for theirs and overwrite the preserved
  # original in _trash.
  if [ -f "$KIT/GatewayKH/README.md" ] \
     && ! cmp -s "$SRC/GatewayKH.md" "$KIT/GatewayKH/README.md"; then
    echo "  superseded: GatewayKH kit README -> _trash/duplicate-kit-$STAMP/"
    run mv "$KIT/GatewayKH/README.md" \
          "$TRASH/duplicate-kit-$STAMP/GatewayKH-kit-README.md"
  fi

  echo "  -- readmes"
  for pair in "README.md:$KIT/README.md" \
              "GatewayKH.md:$KIT/GatewayKH/README.md" \
              "Story Studio.md:$KIT/Story Studio/README.md" \
              "Studio Hub KH.md:$KIT/Studio Hub KH/README.md"; do
    from="$SRC/${pair%%:*}"; to="${pair#*:}"
    if [ ! -f "$from" ]; then echo "  MISSING SOURCE: $from"; continue; fi
    echo "  README -> $to"
    run cp "$from" "$to"
  done

  # Regenerate the gateway kit's checksums from whatever installers are actually present,
  # so this stays correct when the gateway DMG is replaced by a later version. The spare
  # Studio Hub DMG was moved to _trash above, so it no longer appears here.
  GW="$KIT/GatewayKH"
  if [ -d "$GW/installers" ]; then
    echo "  SHA256SUMS.txt regenerated from installers/"
    run bash -c "cd '$GW' && shasum -a 256 installers/*.dmg content-shelf-manifest-*.tsv > SHA256SUMS.txt"
  fi

  # A checksum beside each loose installer, computed from the copy that is actually on the
  # drive, so a reader can prove the drive did not corrupt it. `shasum -c` reads the last
  # line and ignores the leading '#' notes. Every Story Studio installer present is listed:
  # the filename is discovered, so a later build is checksummed without editing this script.
  story_files=()
  for installed in "$KIT/Story Studio"/Story\ Studio\ KH*.dmg; do
    [ -f "$installed" ] && story_files+=("$installed")
  done
  if [ "${#story_files[@]}" -gt 0 ]; then
    echo "  SHA256.txt -> Story Studio/ (${#story_files[@]} installer(s))"
    if [ "$DRY" = "1" ]; then
      echo "  DRY: write $KIT/Story Studio/SHA256.txt"
    else
      for target in "${story_files[@]}"; do
        file="$(basename "$target")"
        hash=$(shasum -a 256 "$target" | awk '{print $1}')
        bytes=$(stat -f%z "$target")
        printf '# %s - installer checksum\n' "$file"
        printf '# %s bytes\n' "$bytes"
        printf '# Verify from this folder with:  shasum -a 256 -c SHA256.txt\n'
        printf '%s  %s\n' "$hash" "$file"
      done > "$KIT/Story Studio/SHA256.txt"
    fi
  fi
done
echo "done (DRY=$DRY)"
