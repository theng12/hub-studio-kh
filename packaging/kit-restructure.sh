#!/bin/bash
# Lay each SSD out as "Video Generation Kit/{GatewayKH,Story Studio,Studio Hub KH,KH Updater}".
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
#
# **The KH Updater folder is laid out from two inputs named by the caller** - `--agent-kit DIR`
# (kh-updater's own `packaging/build-agent-kit.sh` output, credential-free) and
# `--publisher-json FILE` (the publisher's trust anchor) - or the `KHU_AGENT_KIT` /
# `KHU_PUBLISHER_JSON` environment variables. There is no default path for either: a kit build
# reading a fixed path is how a drive gets prepared from last month's agent. Without both, the
# drive gets a README saying the publisher key has not been created and **no install file at all**,
# so nobody can run a half-configured installer.
#
# Inputs of two kinds, two different owners:
#   KH_UPDATER_SOURCE (default packaging/kit-khu)  this repository's installer, apps config, helper
#   AGENT_KIT + PUBLISHER_JSON                    kh-updater's build, and the owner's publishing Mac
set -u

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
SRC="$ROOT_DIR/packaging/kit-readmes"
DRY="${DRY:-0}"
STAMP="${STAMP:-$(date +%Y-%m-%d)}"
KIT_FOLDERS=("Video Generation Kit" "GatewayKH App" "Studio Hub App")
#: This repository's half of the KH Updater folder: the installer, the apps config, the helper.
KH_UPDATER_SOURCE="${KH_UPDATER_SOURCE:-$ROOT_DIR/packaging/kit-khu}"
#: The two inputs the caller names. Empty means "this drive cannot carry an installer yet".
AGENT_KIT="${KHU_AGENT_KIT:-}"
PUBLISHER_JSON="${KHU_PUBLISHER_JSON:-}"
#: **Never part of the kit.** Each drive may carry the owner's plain backup of the publisher's
#: signing key at its root. It is not moved, copied, renamed, listed, checksummed or trashed here,
#: and the digest below proves the folder came out of a run byte for byte as it went in.
PROTECTED_AT_ROOT=("KH Updater signing key backup")

POSITIONAL=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --agent-kit)     [ "$#" -ge 2 ] || { echo "--agent-kit needs a folder" >&2; exit 2; }
                     AGENT_KIT="$2"; shift 2 ;;
    --publisher-json) [ "$#" -ge 2 ] || { echo "--publisher-json needs a file" >&2; exit 2; }
                     PUBLISHER_JSON="$2"; shift 2 ;;
    -h|--help)       sed -n '2,40p' "$0"; exit 0 ;;
    -*)              echo "unknown option: $1" >&2; exit 2 ;;
    *)               POSITIONAL+=("$1"); shift ;;
  esac
done

run() { if [ "$DRY" = "1" ]; then echo "  DRY: $*"; else "$@"; fi; }

is_protected() { # path
  local candidate="$1" root="$2" name
  for name in "${PROTECTED_AT_ROOT[@]}"; do
    case "$candidate" in
      "$root/$name"|"$root/$name"/*) return 0 ;;
    esac
  done
  return 1
}

folder_digest() { # path -> a digest of every name and every byte, or "absent"
  if [ ! -d "$1" ]; then echo "absent"; return 0; fi
  ( cd "$1" || exit 0
    find . -type d -print | LC_ALL=C sort
    find . -type f -print | LC_ALL=C sort | while IFS= read -r item; do
      /usr/bin/shasum -a 256 "$item"
    done
  ) 2>/dev/null | /usr/bin/shasum -a 256 | awk '{print $1}'
}

keep_aside() { # path bucket-name -> move it under this drive's _trash, never overwrite a name there
  local path="$1" name base counter
  if is_protected "$path" "$ROOT"; then echo "  REFUSE (not part of the kit): $path"; return 0; fi
  base="$(basename "$path")"
  name="$base"
  counter=2
  while [ -e "$TRASH/$2-$STAMP/$name" ]; do name="$base-$counter"; counter=$((counter + 1)); done
  echo "  move to _trash/$2-$STAMP/$name: $(basename "$path")"
  run mkdir -p "$TRASH/$2-$STAMP"
  run mv "$path" "$TRASH/$2-$STAMP/$name"
}

DRIVES=()
if [ "${#POSITIONAL[@]}" -gt 0 ]; then
  DRIVES=("${POSITIONAL[@]}")
fi

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

move_into() { # src dst
  local src="$1" dst="$2"
  if is_protected "$src" "${ROOT:-}" || is_protected "$dst" "${ROOT:-}"; then
    echo "  REFUSE (not part of the kit): $src"; return 0
  fi
  if [ ! -e "$src" ]; then echo "  skip (absent): $src"; return 0; fi
  if [ -e "$dst" ]; then echo "  REFUSE (exists): $dst"; return 0; fi
  echo "  move: $src"
  run mv "$src" "$dst"
}

kh_updater_layout() { # $1 = the drive's "Video Generation Kit" folder
  # Two inputs, named by the caller: kh-updater's built agent kit (credential-free) and the
  # publisher's `publisher.json` (the trust anchor). Nothing is read from a fixed path - a kit
  # build that guessed is how a drive gets an agent from last month.
  #
  # **Without a publisher record there is no install file at all.** An agent installed from a kit
  # with no pinned publisher key can never enrol: it would sit on the Mac failing every few
  # minutes and look, from the outside, exactly like a Mac that is simply not reporting. A README
  # that says so is the honest artefact; a runnable installer is not.
  local target="$1/KH Updater" agent="$1/KH Updater/khu-agent" ready=0 stale file
  echo "  -- KH Updater"
  if is_protected "$target" "$ROOT"; then
    echo "  REFUSE (not part of the kit): $target"
    return 0
  fi
  if [ -n "$AGENT_KIT" ] || [ -n "$PUBLISHER_JSON" ]; then
    if [ -d "$AGENT_KIT" ] && [ -f "$PUBLISHER_JSON" ]; then
      ready=1
    else
      echo "  REFUSED: an input named for the KH Updater kit does not exist:"
      [ -d "$AGENT_KIT" ] || echo "    agent kit: ${AGENT_KIT:-<none>}"
      [ -f "$PUBLISHER_JSON" ] || echo "    publisher: ${PUBLISHER_JSON:-<none>}"
      echo "  this drive keeps the README-only layout until both are named and present"
    fi
  else
    echo "  no --agent-kit / --publisher-json: the publisher key has not been created yet"
  fi
  run mkdir -p "$target"
  if [ "$ready" = "1" ]; then
    # A staged agent is replaced whole: `release_kit.py` refuses an existing output rather than
    # merging into one, and the previous copy goes to _trash rather than being overwritten.
    if [ -e "$agent" ] || [ -L "$agent" ]; then
      keep_aside "$agent" "superseded-kh-updater"
    fi
    if [ "$DRY" = "1" ]; then
      echo "  DRY: stage $agent from '$AGENT_KIT' + '$PUBLISHER_JSON'"
    elif python3 "$ROOT_DIR/packaging/release_kit.py" --khu-agent "$AGENT_KIT" \
           --khu-publisher "$PUBLISHER_JSON" --output "$agent" >/dev/null; then
      echo "  agent kit staged: khu-agent/ ($(cat "$agent/VERSION"))"
    else
      echo "  REFUSED: the agent kit was not staged, so no install file is written for it"
      ready=0
    fi
  fi
  if [ "$ready" = "1" ]; then
    for file in "Install KH Updater.command" "khu-install.py" "apps.json"; do
      if [ ! -f "$KH_UPDATER_SOURCE/$file" ]; then
        echo "  MISSING SOURCE: $KH_UPDATER_SOURCE/$file"
        continue
      fi
      echo "  -> KH Updater/$file"
      run cp "$KH_UPDATER_SOURCE/$file" "$target/$file"
      case "$file" in
        *.json) run chmod 644 "$target/$file" ;;   # data, not a program
        *)      run chmod 755 "$target/$file" ;;
      esac
    done
    echo "  README -> $target/README.md"
    run cp "$SRC/KH Updater.md" "$target/README.md"
  else
    # Nothing runnable may survive here. Anything that could be double-clicked is moved out of
    # the way (never deleted), so a drive that lost its publisher record cannot ship an installer
    # that half-works.
    for stale in "Install KH Updater.command" "khu-install.py" "apps.json"; do
      if [ -e "$target/$stale" ]; then
        keep_aside "$target/$stale" "half-configured-kh-updater"
      fi
    done
    if [ -e "$agent" ] || [ -L "$agent" ]; then
      keep_aside "$agent" "half-configured-kh-updater"
    fi
    echo "  README -> $target/README.md (not ready: no publisher key, no installer)"
    run cp "$SRC/KH Updater - not ready yet.md" "$target/README.md"
  fi
}

STATUS=0
for V in "${DRIVES[@]}"; do
  # A bare name is a volume; anything with a separator in it is a path, so this can be pointed at
  # a folder (a test's scratch drive, or a drive mounted somewhere else) without pretending it is
  # under /Volumes. A name that is a path still goes through the same code below.
  case "$V" in
    /*|*/*) ROOT="$V" ;;
    *)     ROOT="/Volumes/$V" ;;
  esac
  echo "===== $V"
  if [ ! -d "$ROOT" ]; then echo "  NOT MOUNTED, skipped"; continue; fi
  KIT="$ROOT/Video Generation Kit"
  TRASH="$ROOT/_trash"
  # The signing key backup is measured before anything is touched and again at the end of this
  # drive's run. It is never read into the kit, never listed and never moved: the digest exists so
  # that a future edit which did would fail loudly here rather than quietly on four drives.
  PROTECTED="$ROOT/${PROTECTED_AT_ROOT[0]}"
  PROTECTED_BEFORE="$(folder_digest "$PROTECTED")"

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
  # `README.md` is the kit index and lives in two places on a prepared drive: the kit's own root
  # and inside `Studio Hub KH/`. `kit_stamp_index.py` stamps the second one and mirrors it over the
  # first, so leaving either without an index is how a drive came to describe a version it did not
  # carry. The Hub's own page is a different document and keeps the name it has on the drives.
  for pair in "README.md:$KIT/README.md" \
              "README.md:$KIT/Studio Hub KH/README.md" \
              "GatewayKH.md:$KIT/GatewayKH/README.md" \
              "Story Studio.md:$KIT/Story Studio/README.md" \
              "Studio Hub KH.md:$KIT/Studio Hub KH/Studio Hub KH.md"; do
    from="$SRC/${pair%%:*}"; to="${pair#*:}"
    if [ ! -f "$from" ]; then echo "  MISSING SOURCE: $from"; continue; fi
    echo "  README -> $to"
    run cp "$from" "$to"
  done

  kh_updater_layout "$KIT"

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

  # **Proved, not promised.** The signing key backup is not part of the kit, and this is the check
  # that says so on every run rather than in a comment: same names, same bytes, or the drive is
  # reported and the script fails. `absent` on both sides means the drive does not carry one.
  if [ "$(folder_digest "$PROTECTED")" != "$PROTECTED_BEFORE" ]; then
    echo "  FAILED: ${PROTECTED_AT_ROOT[0]} changed during this run"
    STATUS=1
  elif [ "$PROTECTED_BEFORE" != "absent" ]; then
    echo "  ${PROTECTED_AT_ROOT[0]}: untouched (it is not part of the kit)"
  fi
done
echo "done (DRY=$DRY)"
exit "$STATUS"
