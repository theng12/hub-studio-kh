#!/bin/bash
# Lay each SSD out as "Video Generation Kit/{GatewayKH,Story Studio,Studio Hub KH}".
#
# Safe by construction: every move is a rename inside one volume, so the model library is
# never copied, and nothing is overwritten - a destination that already exists is refused
# and reported. Duplicates go to the drive's own _trash (never deleted). The readmes come
# from packaging/kit-readmes/ so the layout is reproducible from this repository.
#
#   bash packaging/kit-restructure.sh            # apply
#   DRY=1 bash packaging/kit-restructure.sh      # show what it would do
#
# The GatewayKH guides and SHA256SUMS are repointed at the sibling Studio Hub KH folder,
# because the spare StudioHub DMG inside GatewayKH/installers is a second copy of a kit
# that already ships in full.
set -u

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
SRC="$ROOT_DIR/packaging/kit-readmes"
DRY="${DRY:-0}"
STAMP="${STAMP:-$(date +%Y-%m-%d)}"
DRIVES=("$@")
if [ "${#DRIVES[@]}" -eq 0 ]; then
  DRIVES=(KH-STUDIO-NOVA KH-STUDIO-UGREEN KH-STUDIO-ATLAS KH-STUDIO-ORICO)
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
  move_into "$ROOT/Story Studio KH 1.4.219-arm64.dmg" \
            "$KIT/Story Studio/Story Studio KH 1.4.219-arm64.dmg"

  # Duplicates -> this drive's _trash, keeping the sub-structure.
  run mkdir -p "$TRASH/duplicate-kit-$STAMP"
  if [ -e "$KIT/GatewayKH/installers/StudioHub-0.6.39-12c7d7b.dmg" ]; then
    echo "  duplicate: StudioHub-0.6.39-12c7d7b.dmg -> _trash/duplicate-kit-$STAMP/"
    run mv "$KIT/GatewayKH/installers/StudioHub-0.6.39-12c7d7b.dmg" \
          "$TRASH/duplicate-kit-$STAMP/StudioHub-0.6.39-12c7d7b.dmg"
  fi
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
  # line and ignores the leading '#' notes.
  for pair in "Story Studio:Story Studio KH 1.4.219-arm64.dmg"; do
    dir="${pair%%:*}"; file="${pair#*:}"
    target="$KIT/$dir/$file"
    [ -f "$target" ] || continue
    echo "  SHA256.txt -> $dir/"
    if [ "$DRY" = "1" ]; then
      echo "  DRY: write $KIT/$dir/SHA256.txt"
      continue
    fi
    hash=$(shasum -a 256 "$target" | awk '{print $1}')
    bytes=$(stat -f%z "$target")
    {
      printf '# %s - installer checksum\n' "$file"
      printf '# %s bytes\n' "$bytes"
      printf '# Verify from this folder with:  shasum -a 256 -c SHA256.txt\n'
      printf '%s  %s\n' "$hash" "$file"
    } > "$KIT/$dir/SHA256.txt"
  done
done
echo "done (DRY=$DRY)"
