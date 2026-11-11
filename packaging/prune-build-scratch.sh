#!/bin/bash
# Keep the build scratch bounded, so the disk cannot ratchet the way it did on 2026-09-26.
#
# **What went wrong, because it is the whole reason this file exists.** Every release staged a
# ~2.9 GB app bundle into `.build/StudioHub-<n>` and built a ~1.3 GB DMG, and the previous DMG was
# moved into `_trash/dist/` — where it stayed for ever. Nothing was ever removed, so the internal
# disk only ever filled: on 2026-09-26 it reached **462 MB free of 460 GB**, which refused every
# voice job (`409 OUTPUT_STORAGE_BLOCKED`, the Hub's own 1 GB free-space floor) and made the DMG
# build impossible. The owner reclaimed 38 GB by hand; `.build/` alone held **100.9 GB**, of which
# **32 staged bundles held 70.7 GB**.
#
# **Why the answer is "move it to a drive" and not "delete it" or "put it in `_trash/`".**
#   * `_trash/` is on the same volume, so moving something there frees nothing — that is how the
#     rule intended to protect the owner's work became the thing filling his disk.
#   * A stage is *rebuildable*: `packaging/stage_voice.py` reproduces it from git in about two
#     minutes, and the DMG in `dist/` is the durable artifact.
#   * So the bytes go to a kit drive, verified, and only then does the local copy go away. Nothing
#     is ever deleted without a checked copy existing elsewhere.
#
# Usage:
#   bash packaging/prune-build-scratch.sh --dry-run          # print the plan, change nothing
#   bash packaging/prune-build-scratch.sh                    # pick a mounted kit drive automatically
#   bash packaging/prune-build-scratch.sh --to /Volumes/KH-STUDIO-NOVA
#   bash packaging/prune-build-scratch.sh --keep 2           # also keep the newest two of the rest
#
# **The default is "the latest one", which is the owner's rule.** The stage matching `VERSION` is
# what the installed app and the DMG were built from; every other stage - including a second stage
# of the *same* version, which is what a development re-stage leaves behind - is superseded, and the
# DMG plus the drive archive are its durable copies.
#   bash packaging/prune-build-scratch.sh --cap-trash-dmgs   # also thin `_trash/dist/` (off by default:
#                                                            # `_trash/` is the owner's to sort out)
#
# Exits non-zero without changing anything when there is no drive with room. That is deliberate:
# a half-archived stage is worse than an unpruned one.
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$REPO_ROOT/.build"
TRASH_DIST="$REPO_ROOT/_trash/dist"
STAGE_GLOB="${STAGE_GLOB:-StudioHub-*}"
KEEP_STAGES=0
KEEP_DMGS=1
CAP_TRASH_DMGS=0
DEST_ROOT=""
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --to) DEST_ROOT="${2:?--to needs a volume}"; shift 2 ;;
    --keep) KEEP_STAGES="${2:?--keep needs a number}"; shift 2 ;;
    --keep-dmgs) KEEP_DMGS="${2:?--keep-dmgs needs a number}"; shift 2 ;;
    # **`_trash/` is the owner's, and the rule says never tidy inside it.** Capping the DMG history
    # that `make-dmg.sh` keeps there is therefore opt-in: without this flag the script does not look
    # at `_trash/` at all. `.build/` is different - it is gitignored scratch, not a record.
    --cap-trash-dmgs) CAP_TRASH_DMGS=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# **Newest by mtime is not a safe rule here, and it was measured failing.** `chmod -R u+w` while
# reading a stage updates the stage directory's own mtime, so a "keep the newest" pass picked a
# 0.6.71 bundle and proposed moving the 0.6.74 stage this release was built from. A stage's version
# is the fact that matters, so it is read from the stage itself.
stage_version() {
  # A stage is a directory holding `Hub Studio KH.app`, so the manifest is one level deeper. Getting
  # this wrong read every stage as "unknown", which meant no stage was recognised as the current
  # release and the 0.6.74 stage was queued for pruning - the one thing this must never do.
  local manifest="$1/Hub Studio KH.app/Contents/Resources/release-manifest.json"
  [ -f "$manifest" ] || manifest="$1/Contents/Resources/release-manifest.json"
  [ -f "$manifest" ] || return 0
  sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$manifest" | head -1
}
version_key() { local digits; digits="$(printf '%s' "$1" | tr -cd '0-9')"; echo "$(( 10#${digits:-0} ))"; }
stage_build() {
  local plist="$1/Hub Studio KH.app/Contents/Info.plist"
  [ -f "$plist" ] || plist="$1/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c 'Print :StudioHubBuild' "$plist" 2>/dev/null || true
}
stage_is_dev() {
  local manifest="$1/Hub Studio KH.app/Contents/Resources/release-manifest.json"
  [ -f "$manifest" ] || manifest="$1/Contents/Resources/release-manifest.json"
  grep -q '"development_build"[[:space:]]*:[[:space:]]*true' "$manifest" 2>/dev/null
}
# What the running app was installed from. Beats every version comparison: two stages can carry the
# same version - a development re-stage does exactly that - and only the stamp says which one is
# deployed.
INSTALLED_STAMP="$(/usr/libexec/PlistBuddy -c 'Print :StudioHubBuild' "/Applications/Hub Studio KH.app/Contents/Info.plist" 2>/dev/null || true)"

CURRENT_VERSION="$(cat "$REPO_ROOT/VERSION" 2>/dev/null | tr -d '[:space:]')"
ranked="$(mktemp)"
for d in "$BUILD_DIR"/$STAGE_GLOB; do
  [ -d "$d" ] || continue
  v="$(stage_version "$d")"
  printf '%s\t%s\t%s\n' "$(version_key "$v")" "${v:-unknown}" "$d" >> "$ranked"
done
ranked_sorted="$(mktemp)"
if ! LC_ALL=C sort -k1,1nr -k2,2r "$ranked" > "$ranked_sorted" 2>/dev/null; then cp "$ranked" "$ranked_sorted"; fi

# **One stage is kept, and which one is decided by evidence, not by a name.**
#   1. the stage whose build stamp is what `/Applications` has - it is what the DMG was built from;
#   2. failing that, the newest stage of the release in `VERSION` that is not a development build;
#   3. failing that, the newest stage of `VERSION`.
# A development re-stage of the same version is superseded by definition, which is why "same
# version" alone is not enough: 0.6.75 left both a `0675` (dev) and a `0675b` (the release).
kept_for_release=()
stages=()
keep_path=""
while IFS=$'\t' read -r _key _ver path; do
  [ -n "$path" ] || continue
  if [ -z "$keep_path" ] && [ -n "$INSTALLED_STAMP" ] && [ "$(stage_build "$path")" = "$INSTALLED_STAMP" ]; then
    keep_path="$path"; kept_for_release+=("$path"); continue
  fi
  stages+=("$path")
done < "$ranked_sorted"

if [ -z "$keep_path" ]; then
  for want_dev in false true; do
    while IFS=$'\t' read -r _key _ver path; do
      [ -n "$path" ] || continue
      [ "$_ver" = "$CURRENT_VERSION" ] || continue
      if [ "$want_dev" = true ] || ! stage_is_dev "$path"; then
        keep_path="$path"; break
      fi
    done < "$ranked_sorted"
    [ -n "$keep_path" ] && break
  done
  if [ -n "$keep_path" ]; then
    remaining=()
    for candidate in "${stages[@]:-}"; do
      [ -n "$candidate" ] || continue
      [ "$candidate" = "$keep_path" ] || remaining+=("$candidate")
    done
    stages=("${remaining[@]:-}")
    kept_for_release=("$keep_path")
  fi
fi
rm -f "$ranked" "$ranked_sorted"

dmg_candidates=()
if [ "$CAP_TRASH_DMGS" -eq 1 ] && [ -d "$TRASH_DIST" ]; then
  while IFS= read -r line; do [ -n "$line" ] && dmg_candidates+=("$line"); done < <(ls -dt "$TRASH_DIST"/*.dmg 2>/dev/null)
fi

stages_to_move=("${stages[@]:KEEP_STAGES}")
dmgs_to_move=("${dmg_candidates[@]:KEEP_DMGS}")

# `du` on a stage that is being read or written elsewhere returns nothing, and an empty operand is
# a bash arithmetic error - which killed this script on its first dry run. Default every figure.
size_kb() { local kb; kb="$(du -sk "$1" 2>/dev/null | cut -f1)"; echo "${kb:-0}"; }
count_of() { local n; n="$(printf '%s\n' "$@" | grep -c . 2>/dev/null)"; echo "${n:-0}"; }

need_kb=0
for d in "${stages_to_move[@]:-}"; do [ -n "$d" ] && need_kb=$(( need_kb + $(size_kb "$d") )); done
for f in "${dmgs_to_move[@]:-}"; do [ -n "$f" ] && need_kb=$(( need_kb + $(size_kb "$f") )); done

echo "stages on disk: ${#stages[@]} + ${#kept_for_release[@]} for the current release ($CURRENT_VERSION)"
echo "  keeping  : ${keep_path:-none} (stamp ${INSTALLED_STAMP:-unknown}) and the newest $KEEP_STAGES of the rest"
echo "  moving   : $(count_of "${stages_to_move[@]:-}") stages"
if [ "$CAP_TRASH_DMGS" -eq 1 ]; then
  echo "old DMGs in _trash/dist: ${#dmg_candidates[@]} | keeping the newest $KEEP_DMGS, moving $(count_of "${dmgs_to_move[@]:-}")"
else
  echo "old DMGs in _trash/dist: left alone (--cap-trash-dmgs to include them; _trash/ is the owner's)"
fi
echo "to move: $(( need_kb / 1048576 )) GB"

if [ "$need_kb" -eq 0 ]; then echo "nothing to do."; exit 0; fi

if [ -z "$DEST_ROOT" ]; then
  # **Prefer the drive that already holds the most of this repo's archive**, so the history stays in
  # one place. Size rather than mere presence: a stray partial copy on another drive must not win the
  # choice, which is exactly what happened when two drives both had an `_build-archive/` directory.
  best_kb=-1
  for candidate in /Volumes/KH-STUDIO-*; do
    archive="$candidate/_build-archive/$(basename "$REPO_ROOT")"
    [ -d "$archive" ] || continue
    have_kb=$(du -sk "$archive" 2>/dev/null | cut -f1); have_kb=${have_kb:-0}
    free_kb=$(df -Pk "$candidate" 2>/dev/null | awk 'NR == 2 {print $4}')
    [ -n "$free_kb" ] || continue
    [ "$free_kb" -gt $(( need_kb + need_kb / 10 )) ] || continue
    if [ "$have_kb" -gt "$best_kb" ]; then best_kb="$have_kb"; DEST_ROOT="$candidate"; fi
  done
  # 10% headroom over what has to land, so a drive is never filled to the last byte. Only reached
  # when the loop above found nothing - it used to run unconditionally and overwrite the choice.
  for candidate in /Volumes/KH-STUDIO-*; do
    [ -z "$DEST_ROOT" ] || break
    [ -d "$candidate" ] || continue
    free_kb=$(df -Pk "$candidate" 2>/dev/null | awk 'NR == 2 {print $4}')
    [ -n "$free_kb" ] || continue
    if [ "$free_kb" -gt $(( need_kb + need_kb / 10 )) ]; then DEST_ROOT="$candidate"; break; fi
  done
fi
if [ -z "$DEST_ROOT" ]; then
  echo "no mounted kit drive has room for $(( need_kb / 1048576 )) GB; nothing was touched." >&2
  echo "Free space on a drive, or pass --to <volume>." >&2
  exit 1
fi

DEST="$DEST_ROOT/_build-archive/$(basename "$REPO_ROOT")"
echo "destination: $DEST"
if [ "$DRY_RUN" -eq 1 ]; then
  printf '  would move %s\n' "${stages_to_move[@]:-}" "${dmgs_to_move[@]:-}" | grep -v '^  would move $'
  echo "(dry run - nothing was changed)"; exit 0
fi

manifest() { ( cd "$1" && find . -type f -exec shasum -a 256 {} + 2>/dev/null | LC_ALL=C sort ); }

move_verified() {  # <path> <destination directory>
  local src="$1" destdir="$2" name; name="$(basename "$src")"
  mkdir -p "$destdir"
  if [ ! -e "$destdir/$name" ]; then
    # **`ditto`'s exit code is advisory, and treating it as final was a real bug here.** It returns
    # non-zero when it cannot preserve a *symlink's* mode — the image runtime ships `bin/python3`,
    # `pkgconfig/python3.pc` and `share/man/man1/python3.1` as links — on a copy that is otherwise
    # complete. The first real run of this script "failed" both stages for that reason and left them
    # in place. The sha256 manifest check below is the test that decides; this only reports.
    if [ -d "$src" ]; then
      ditto "$src" "$destdir/$name" 2>/dev/null || echo "  note: ditto reported an attribute warning for $name; verifying anyway"
    elif ! cp -p "$src" "$destdir/$name" 2>/dev/null; then
      echo "  COPY FAILED $name (left in place)"; return 1
    fi
  fi
  # A copy is not a copy until it is checked. Entry lists *and* content: `du` matched on a truncated
  # tree once, which is exactly the mistake this guards against.
  if [ -d "$src" ]; then
    if ! diff <(manifest "$src") <(manifest "$destdir/$name") >/dev/null 2>&1; then
      echo "  MISMATCH $name (left in place; the archived copy is suspect)"; return 1
    fi
  elif ! cmp -s "$src" "$destdir/$name"; then
    echo "  MISMATCH $name (left in place)"; return 1
  fi
  # **`rm -rf` alone cannot delete these.** The packaged `runtimes/image/lib/tcl9` directories are
  # `dr-xr-xr-x` and the music runtime ships `r--r--r--` files, and unlinking an entry needs write
  # permission on its parent — so the tree survives and the stage looks undeletable. Measured twice
  # on 2026-09-26: once here, once in `install_local.sh`.
  chmod -R u+w "$src" 2>/dev/null || true
  rm -rf "$src" 2>/dev/null || true
  [ -e "$src" ] && { echo "  VERIFIED but could not be removed: $name"; return 1; }
  echo "  moved $name"
  return 0
}

failed=0
for d in "${stages_to_move[@]:-}"; do [ -n "$d" ] && { move_verified "$d" "$DEST" || failed=$((failed+1)); }; done
for f in "${dmgs_to_move[@]:-}"; do [ -n "$f" ] && { move_verified "$f" "$DEST/dist" || failed=$((failed+1)); }; done

echo "disk free now: $(df -h /System/Volumes/Data | tail -1 | awk '{print $4}')"
[ "$failed" -eq 0 ] || { echo "$failed item(s) were left in place" >&2; exit 1; }
exit 0
