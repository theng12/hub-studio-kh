#!/bin/bash
# Z-Image-Turbo 6B MLX 4-bit on an 8 GB Mac — the one tier that is still unmeasured.
#
#   bash eight-gb-z-image-run.sh
#
# Nothing to edit and nothing to upload. The script finds the 4-bit pack (the kit drive,
# ~/StudioHubModels or the HF cache), finds the engine (the runtime inside the installed Studio
# Hub app first, else a uv/pipx mflux), checks that this machine is the right one, runs four
# generations and prints one short block to paste back. Output lands in
# ~/Desktop/StudioHub Z-Image 8GB Run/ as VERDICT.txt, results.tsv, logs/ and the images.
#
# Why an 8 GB Mac and not the 32 GB one: on a big machine MLX's buffer cache inflates the peak
# into whatever RAM is free, so the 17.7-20.4 GB "default" footprint measured on the 32 GiB M2 Max
# (qualification/z-image-turbo-2026-09-24.md) is partly an artefact of having room to grow. The
# only honest answer comes from a real 8 GiB M1/M2, which is what this script is for. The 16 GiB
# tier is already covered by the model's own rule (low-RAM below 24 GB, Z_IMAGE_LOW_RAM_BELOW_BYTES
# in components/image/app/backend/generation.py); this measures whether 8 GiB does the same.
#
# It refuses, rather than guessing, and says which check failed with the measured reason:
#   * this is not an 8 GB Mac          (a bigger host answers a different question)
#   * too little memory free at start  (below the model's published free floor, 3.0 GB)
#   * the engine is not mflux 0.20.0   (the version this model was qualified on)
#   * the 4-bit pack is incomplete     (transformer/, text_encoder/, tokenizer/, vae/)
# A run that is stopped (swap cap, wall limit) is recorded as ABORTED, not as a failure: an
# aborted run neither proves nor disproves the tier, and the verdict says so.
#
# Close your other apps first, and keep the Mac on power. On 8 GB this is genuinely tight, and
# the point of the exercise is to find out how tight, so a busy machine would measure the wrong
# thing.
#
# Knobs (the defaults are right for the one-command run; all are optional):
#   ZIMAGE_SMOKE=1               harness self-check only: one 512x512 / 2-step run into a temp
#                                folder. Never touches the real record, prints no verdict.
#   ZIMAGE_ALLOW_NON_TARGET=1    run on a Mac that is not the 8 GB tier anyway (the block then
#                                says the result is not 8 GB evidence).
#   ZIMAGE_ALLOW_OTHER_ENGINE=1  accept a mflux that is not 0.20.x (not comparable with the
#                                32 GB record; the version that ran is recorded).
#   ZIMAGE_IGNORE_FREE_FLOOR=1   attempt the run below the published free-memory floor.
#   ZIMAGE_MIN_FREE_GB=3.0       the free-memory floor (the model's own published value).
#   ZIMAGE_MAX_SWAP_GB=6         stop a run whose system swap passes this.
#   ZIMAGE_TIMEOUT=1800          stop a single run after this many seconds.
#   ZIMAGE_MFLUX_BIN=...         use this mflux-generate-z-image-turbo.
#   ZIMAGE_PACK=...              use this pack folder (must contain transformer/ etc.).
#   ZIMAGE_OUT=...               write the record somewhere other than ~/Desktop (the script's
#                                own tests use this; the default is where the owner looks).
set -u

# A double-clicked or pasted script can arrive with a minimal PATH. Put the system directories
# back first, and keep whatever PATH came in after them so a user-installed mflux is still found.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

# ── tools, by absolute path ────────────────────────────────────────────────────────
# `sysctl` and `vm_stat` are the two that matter: neither is on every PATH, and a bare `sysctl`
# that silently failed once made the swap sampler report a peak of 0 MB and the host line read
# "unknown" — a zero that looks like a reading. Where a tool is genuinely missing, the record
# says so and every figure that needed it is written as "?" instead of 0.
ABS() { local p; for p in "$@"; do [ -x "$p" ] && { printf '%s\n' "$p"; return 0; }; done; return 1; }
SYSCTL="$(ABS /usr/sbin/sysctl /usr/bin/sysctl || true)"
VMSTAT="$(ABS /usr/bin/vm_stat || true)"
TIME="$(ABS /usr/bin/time || true)"
STAT="$(ABS /usr/bin/stat || true)"
PKILL="$(ABS /usr/bin/pkill || true)"
PGREP="$(ABS /usr/bin/pgrep || true)"
MKTEMP="$(ABS /usr/bin/mktemp || true)"
SHASUM="$(ABS /usr/bin/shasum || true)"
COLUMN="$(ABS /usr/bin/column || true)"
SW_VERS="$(ABS /usr/bin/sw_vers || true)"
DU="$(ABS /usr/bin/du || true)"
DATE="$(ABS /bin/date /usr/bin/date || true)"
SYSPROF="$(ABS /usr/sbin/system_profiler /usr/bin/system_profiler || true)"
[ -n "$DATE" ] || { echo "no date(1) at /bin/date" >&2; exit 1; }

# ── this machine ───────────────────────────────────────────────────────────────────
CHIP=""; MODEL_ID=""; MEM_BYTES=""; MEM_GB="unknown"; PAGESIZE=""
if [ -n "$SYSCTL" ]; then
  CHIP="$("$SYSCTL" -n machdep.cpu.brand_string 2>/dev/null)"
  MEM_BYTES="$("$SYSCTL" -n hw.memsize 2>/dev/null)"
  MODEL_ID="$("$SYSCTL" -n hw.model 2>/dev/null)"
  PAGESIZE="$("$SYSCTL" -n hw.pagesize 2>/dev/null)"
fi
case "$MEM_BYTES" in ''|*[!0-9]*) MEM_BYTES="" ;; esac
case "$PAGESIZE" in ''|*[!0-9]*) PAGESIZE=16384 ;; esac
if [ -n "$MEM_BYTES" ]; then
  MEM_GB="$(awk -v b="$MEM_BYTES" 'BEGIN{printf "%.1f", b/1073741824}')"
elif [ -n "$SYSPROF" ]; then
  # Apple Silicon does not always publish machdep.cpu.brand_string in a restricted environment;
  # system_profiler knows the same facts and is only consulted when sysctl came up empty.
  MEM_GB="$("$SYSPROF" SPHardwareDataType 2>/dev/null | awk -F': ' '/^ *Memory:/{print $2; exit}')"
  [ -n "$MEM_GB" ] || MEM_GB="unknown"
fi
[ -n "$CHIP" ] || CHIP="$("$SYSPROF" SPHardwareDataType 2>/dev/null | awk -F': ' '/^ *Chip:/{print $2; exit}')"
[ -n "$CHIP" ] || CHIP="${MODEL_ID:-unknown}"
OSV="$("$SW_VERS" -productVersion 2>/dev/null || echo unknown)"

# The 8 GB tier is 8 GiB = 8589934592 bytes; the next tier is 16 GiB, so a 9 GiB boundary cannot
# misclassify a real machine. An unreadable memory size is "unknown", which is a refusal in a real
# run rather than a silent pass.
TARGET_MAX_BYTES=$((9 * 1024 * 1024 * 1024))
ON_TARGET=unknown
if [ -n "$MEM_BYTES" ]; then
  if [ "$MEM_BYTES" -le "$TARGET_MAX_BYTES" ]; then ON_TARGET=yes; else ON_TARGET=no; fi
elif [ "$MEM_GB" != unknown ]; then
  ON_TARGET="$(awk -v g="$MEM_GB" 'BEGIN{print (g+0 <= 9) ? "yes" : "no"}')"
fi

# ── the guards ─────────────────────────────────────────────────────────────────────
# The target-host check comes before anything is created: a refusal must leave no folder and no
# results file behind, or a refused run could be mistaken for a run that happened and produced
# nothing. (It did exactly that in testing: the folder and an empty results.tsv appeared on the
# Desktop while the script was printing "nothing was generated".)
if [ "${ZIMAGE_SMOKE:-0}" != "1" ] && [ "$ON_TARGET" != yes ] && [ "${ZIMAGE_ALLOW_NON_TARGET:-0}" != 1 ]; then
  cat >&2 <<EOF
REFUSING TO RUN: this script measures the 8 GB tier, and this Mac reports ${MEM_GB} GB of
unified memory (target: an 8 GiB M1/M2). On a bigger Mac MLX grows its buffer cache into the
free RAM, so the peak measured here would be a property of this host, not of the tier.

  * Run it on the 8 GB Mac — that is the whole point of the exercise.
  * Harness self-check on this Mac instead:   ZIMAGE_SMOKE=1 bash "$0"
  * Run anyway, accepting the result is NOT 8 GB evidence:
        ZIMAGE_ALLOW_NON_TARGET=1 bash "$0"

Nothing was generated and no record was written.
EOF
  exit 4
fi

# ── where the record goes ──────────────────────────────────────────────────────────
# Smoke mode is decided here, before anything is created: it exists to prove the harness cheaply,
# so it must not touch the real output folder at all. It used to be decided further down, after
# the Desktop folder had been created and results.tsv truncated — which meant running the smoke
# check after a real run would have thrown the real results away.
if [ "${ZIMAGE_SMOKE:-0}" = "1" ]; then
  SMOKE=1
  if [ -n "$MKTEMP" ]; then SMOKE_TMP="$("$MKTEMP" -d)"; else SMOKE_TMP="${TMPDIR:-/tmp}/z-image-smoke-$$"; mkdir -p "$SMOKE_TMP"; fi
  OUT="$SMOKE_TMP/Z-Image Smoke"
else
  SMOKE=0
  OUT="${ZIMAGE_OUT:-$HOME/Desktop/StudioHub Z-Image 8GB Run}"
fi
LOGS="$OUT/logs"
mkdir -p "$LOGS" || { echo "cannot create $LOGS" >&2; exit 1; }
RESULTS="$OUT/results.tsv"
# Never throw a previous record away silently: move it aside with its time in the name.
if [ -s "$RESULTS" ]; then mv "$RESULTS" "$OUT/results-$(date -u '+%Y%m%dT%H%M%SZ').tsv"; fi
: > "$RESULTS"

# ── the engine: mflux 0.20.0, the version this model was qualified on ──────────────
# The app carries its own image runtime, so a Mac with the kit's app installed needs no network
# and no uv/pipx at all. That copy is preferred: it is the engine the app itself runs.
MFLUX=""
for candidate in \
  "${ZIMAGE_MFLUX_BIN:-}" \
  /Applications/"Studio Hub.app/Contents/Resources/runtimes/image/bin/mflux-generate-z-image-turbo" \
  "$HOME"/Applications/"Studio Hub.app/Contents/Resources/runtimes/image/bin/mflux-generate-z-image-turbo" \
  "$HOME"/.local/bin/mflux-generate-z-image-turbo; do
  [ -n "$candidate" ] && [ -x "$candidate" ] && { MFLUX="$candidate"; break; }
done
if [ -z "$MFLUX" ]; then
  # A copy of the app on a mounted kit drive, wherever the drive is mounted and whatever the
  # folders above the bundle are called.
  for candidate in /Volumes/*/*/"Studio Hub.app"/Contents/Resources/runtimes/image/bin/mflux-generate-z-image-turbo \
                   /Volumes/*/*/*/"Studio Hub.app"/Contents/Resources/runtimes/image/bin/mflux-generate-z-image-turbo; do
    [ -x "$candidate" ] && { MFLUX="$candidate"; break; }
  done
fi
if [ -z "$MFLUX" ]; then
  candidate="$(command -v mflux-generate-z-image-turbo 2>/dev/null)"
  [ -n "$candidate" ] && [ -x "$candidate" ] && MFLUX="$candidate"
fi
if [ -z "$MFLUX" ]; then
  echo "mflux was not found in an installed Studio Hub app or on PATH."
  echo "Installing mflux==0.20.0 (the version Z-Image-Turbo 6B MLX 4-bit was qualified on)…"
  if command -v uv >/dev/null 2>&1; then
    uv tool install mflux==0.20.0 || { echo "install failed" >&2; exit 1; }
  elif command -v pipx >/dev/null 2>&1; then
    pipx install mflux==0.20.0 || { echo "install failed" >&2; exit 1; }
  else
    echo "Neither uv nor pipx is present, and no Studio Hub app was found." >&2
    echo "Install the kit's app (it carries this runtime), or install uv, then re-run." >&2
    exit 1
  fi
  export PATH="$HOME/.local/bin:$PATH"
  candidate="$(command -v mflux-generate-z-image-turbo 2>/dev/null)"
  [ -n "$candidate" ] && [ -x "$candidate" ] || { echo "install did not produce mflux-generate-z-image-turbo" >&2; exit 1; }
  MFLUX="$candidate"
fi

engine_python() { # the interpreter that goes with the engine, if it can decode a PNG
  local dir py; dir="$(dirname "$MFLUX")"
  for py in "$dir/python3" \
            "$HOME/.local/share/uv/tools/mflux/bin/python3" \
            "$HOME/.local/pipx/venvs/mflux/bin/python3"; do
    [ -x "$py" ] || continue
    "$py" -c 'import PIL' >/dev/null 2>&1 && { printf '%s\n' "$py"; return 0; }
  done
  return 1
}
engine_version() { # mflux's own version, from its dist-info or its interpreter
  local dir v; dir="$(dirname "$MFLUX")"
  local py; py="$(engine_python || true)"
  if [ -n "$py" ]; then
    v="$("$py" -c 'import importlib.metadata as m; print(m.version("mflux"))' 2>/dev/null)"
    [ -n "$v" ] && { printf '%s\n' "$v"; return 0; }
  fi
  for v in "$dir"/../lib/python*/site-packages/mflux-*.dist-info \
           "$HOME"/.local/share/uv/tools/mflux/lib/python*/site-packages/mflux-*.dist-info \
           "$HOME"/.local/pipx/venvs/mflux/lib/python*/site-packages/mflux-*.dist-info; do
    [ -e "$v" ] || continue
    basename "$v" | sed -E 's/^mflux-//; s/\.dist-info$//'
    return 0
  done
  v="$(uv tool list 2>/dev/null | awk '/^mflux /{print $2; exit}')"
  [ -n "$v" ] && { printf '%s\n' "${v#v}"; return 0; }
  v="$(pipx list --short 2>/dev/null | awk '/^mflux /{print $2; exit}')"
  [ -n "$v" ] && { printf '%s\n' "$v"; return 0; }
  printf 'unknown\n'
}
MFVER="$(engine_version)"
PY_IMG="$(engine_python || true)"

case "$MFVER" in
  0.20.*) ;;
  *)
    if [ "$SMOKE" = 0 ] && [ "${ZIMAGE_ALLOW_OTHER_ENGINE:-0}" != 1 ]; then
      cat >&2 <<EOF
REFUSING TO RUN: the engine found is mflux ${MFVER}, not the 0.20.0 this model was qualified on
(qualification/z-image-turbo-2026-09-24.md). Timings and memory from another engine version
cannot be compared with the 32 GB record, and the tier verdict is about this model on this engine.

  Engine: $MFLUX
  This advice used to read "install the kit's app (it carries mflux 0.20.0)". That was false, and
  it sent the owner to reinstall for nothing: releases 0.6.63 to 0.6.67 shipped mflux 0.17.5 after
  0.6.59 to 0.6.62 had shipped 0.20.0, because the release loop staged the superseded runtime. The
  kit on a drive made before 0.6.68 carries 0.17.5 too. Check what THIS app carries before acting:

      ls "/Applications/Studio Hub.app/Contents/Resources/runtimes/image"/lib/python*/site-packages \
        | grep -o 'mflux-[0-9.]*'

  Fix:    a kit at 0.6.68 or later carries mflux 0.20.0 - install that app, or
          uv tool install --force mflux==0.20.0
  Or accept a non-comparable run:  ZIMAGE_ALLOW_OTHER_ENGINE=1 bash "$0"

Nothing was generated and no record was written.
EOF
      exit 5
    fi
    echo "WARNING: mflux ${MFVER} is not 0.20.x — these numbers will not be comparable with the 32 GB record." >&2
    ;;
esac

# ── the 4-bit pack ─────────────────────────────────────────────────────────────────
PACK="${1:-${ZIMAGE_PACK:-}}"
pack_ok() { [ -d "$1/transformer" ] && [ -d "$1/text_encoder" ] && [ -d "$1/tokenizer" ] && [ -d "$1/vae" ]; }
if [ -n "$PACK" ] && ! pack_ok "$PACK"; then
  echo "The pack passed in is not complete: $PACK" >&2
  for sub in transformer text_encoder tokenizer vae; do
    [ -d "$PACK/$sub" ] || echo "  missing: $sub/" >&2
  done
  exit 1
fi
if [ -z "$PACK" ]; then
  # The library location comes first — a local copy is faster and does not depend on the drive
  # staying mounted for the whole run. Z-Image became a shipped model in 0.6.59 and the drive
  # copy moved from optional-models/ into model-library/<package>/<revision>/model; the old path
  # is kept so this still runs against a drive that has not been restocked yet.
  for candidate in \
    "$HOME"/StudioHubModels/z-image-turbo-mflux-4bit/*/model \
    /Volumes/*/*/model-library/z-image-turbo-mflux-4bit/*/model \
    /Volumes/*/*/*/model-library/z-image-turbo-mflux-4bit/*/model \
    /Volumes/*/*/*/*/model-library/z-image-turbo-mflux-4bit/*/model \
    /Volumes/*/*/optional-models/z-image-turbo-mflux-4bit \
    /Volumes/*/*/*/optional-models/z-image-turbo-mflux-4bit \
    "$HOME"/.cache/huggingface/hub/models--filipstrand--Z-Image-Turbo-mflux-4bit/snapshots/*; do
    if pack_ok "$candidate"; then PACK="$candidate"; break; fi
  done
fi
if [ -z "$PACK" ]; then
  cat >&2 <<'EOF'
Could not find the Z-Image-Turbo 6B MLX 4-bit pack.

Plug in the kit drive (the script looks for model-library/z-image-turbo-mflux-4bit/... on any
mounted volume), or pass the folder explicitly:
  bash eight-gb-z-image-run.sh "/Volumes/<DRIVE>/…/z-image-turbo-mflux-4bit/<revision>/model"

It must be a folder containing transformer/, text_encoder/, tokenizer/ and vae/.
EOF
  exit 1
fi
PACK_REV="$(basename "$(dirname "$PACK")")"
case "$PACK_REV" in
  *[!0-9a-f]*|"") PACK_REV=unknown ;;
  *) [ "${#PACK_REV}" -eq 40 ] || PACK_REV=unknown ;;
esac

# ── free memory at start ───────────────────────────────────────────────────────────
# vm_stat is the only honest source for this: "free" alone is misleading on macOS because
# inactive and speculative pages are reclaimable. Available = free + inactive + speculative +
# purgeable, which is what the model has to work with before macOS starts compressing and
# swapping. The floor is the model's own published min_free_memory_gb (3.0), the same number the
# Hub's admission governor enforces before it dispatches a job — and it is compared in decimal
# GB. Since 0.6.125 the Hub's own figure (components/host_memory.py) also counts the file cache, so it
# can be higher than this one: a machine this script passes is a machine the Hub would dispatch to.
vm_pages() {
  [ -n "$VMSTAT" ] || return 1
  "$VMSTAT" 2>/dev/null | awk -F: -v want="$1" '
    { k=$1; gsub(/^[ \t]+|[ \t]+$/,"",k) }
    k==want { v=$2; gsub(/[^0-9]/,"",v); print v+0; found=1; exit }
    END { exit(found?0:1) }'
}
GB_FROM_PAGES() { awk -v p="$1" -v s="$PAGESIZE" 'BEGIN{printf "%.2f", p*s/1e9}'; }
FREE_PAGES="$(vm_pages 'Pages free' || true)"
INACTIVE_PAGES="$(vm_pages 'Pages inactive' || true)"
SPEC_PAGES="$(vm_pages 'Pages speculative' || true)"
PURGE_PAGES="$(vm_pages 'Pages purgeable' || true)"
COMP_PAGES="$(vm_pages 'Pages occupied by compressor' || true)"
WIRED_PAGES="$(vm_pages 'Pages wired down' || true)"
if [ -n "${FREE_PAGES:-}" ]; then
  FREE_GB="$(GB_FROM_PAGES "$FREE_PAGES")"
  INACTIVE_GB="$(GB_FROM_PAGES "${INACTIVE_PAGES:-0}")"
  SPEC_GB="$(GB_FROM_PAGES "${SPEC_PAGES:-0}")"
  PURGE_GB="$(GB_FROM_PAGES "${PURGE_PAGES:-0}")"
  AVAIL_BYTES="$(( (FREE_PAGES + ${INACTIVE_PAGES:-0} + ${SPEC_PAGES:-0} + ${PURGE_PAGES:-0}) * PAGESIZE ))"
  AVAIL_GB="$(awk -v b="$AVAIL_BYTES" 'BEGIN{printf "%.2f", b/1e9}')"
else
  FREE_GB="?"; INACTIVE_GB="?"; SPEC_GB="?"; PURGE_GB="?"; AVAIL_BYTES=""; AVAIL_GB="?"
fi
[ -n "${COMP_PAGES:-}" ] && COMP_GB="$(GB_FROM_PAGES "$COMP_PAGES")" || COMP_GB="?"
[ -n "${WIRED_PAGES:-}" ] && WIRED_GB="$(GB_FROM_PAGES "$WIRED_PAGES")" || WIRED_GB="?"

swap_used_mb() {
  [ -n "$SYSCTL" ] || return 1
  "$SYSCTL" -n vm.swapusage 2>/dev/null | awk '
    { for (i=1;i<=NF;i++) if ($i=="used") { v=$(i+2); sub(/M$/,"",v); if (v ~ /^[0-9.]+$/) { print v; found=1 } } }
    END { exit(found?0:1) }'
}
SWAP_START_MB="$(swap_used_mb || true)"

MIN_FREE_GB="${ZIMAGE_MIN_FREE_GB:-3.0}"
# The mode the app itself would choose, from its own published rule: below
# Z_IMAGE_LOW_RAM_BELOW_BYTES (24 GB) the worker runs mflux with --low-ram
# (components/image/app/backend/generation.py). Stated rather than assumed, so a refusal on a
# machine the rule would treat differently does not claim the wrong mode.
if [ -n "$MEM_BYTES" ] && [ "$MEM_BYTES" -gt 0 ]; then
  if [ "$MEM_BYTES" -lt 24000000000 ]; then APP_MODE="low-ram"; else APP_MODE="default"; fi
elif [ "$MEM_GB" != unknown ] && awk -v g="$MEM_GB" 'BEGIN{exit !(g+0 > 0)}'; then
  if awk -v g="$MEM_GB" 'BEGIN{exit !(g+0 < 24)}'; then APP_MODE="low-ram"; else APP_MODE="default"; fi
else
  APP_MODE="unknown"
fi
ABORT_PREFLIGHT=0
if [ "$SMOKE" = 0 ] && [ -n "$AVAIL_BYTES" ] && [ "${ZIMAGE_IGNORE_FREE_FLOOR:-0}" != 1 ]; then
  if awk -v b="$AVAIL_BYTES" -v f="$MIN_FREE_GB" 'BEGIN{exit !(b < f*1e9)}'; then ABORT_PREFLIGHT=1; fi
fi

{
  echo "Host: $CHIP · $MEM_GB GB · macOS $OSV · model ${MODEL_ID:-unknown}"
  echo "Engine: mflux $MFVER · $MFLUX"
  echo "Pack: $PACK"
  echo "Pack size: $("$DU" -sk "$PACK" 2>/dev/null | awk '{printf "%.2f GB", $1*1024/1e9}')"
  echo "Memory at start: ${AVAIL_GB:-?} GB available (free $FREE_GB + inactive $INACTIVE_GB + speculative $SPEC_GB + purgeable $PURGE_GB), wired $WIRED_GB GB, compressor $COMP_GB GB, swap used ${SWAP_START_MB:-?} MB"
  echo "Free-memory floor: $MIN_FREE_GB GB (min_free_memory_gb in components/image/app/backend/generation.py)"
  echo "Date: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
} | tee "$OUT/host.txt"

if [ -z "$VMSTAT" ]; then
  echo "WARNING: vm_stat unavailable — free memory and compressor figures are NOT readings." >&2
fi

if [ "$ABORT_PREFLIGHT" = 1 ]; then
  {
    echo "==================== COPY FROM HERE ===================="
    echo "8 GB TIER · Z-Image-Turbo 6B MLX 4-bit — NOT RUN"
    echo "Machine: $CHIP · $MEM_GB GB unified memory · macOS $OSV"
    [ "$ON_TARGET" = yes ] || echo "WARNING: this Mac is not the 8 GB tier, so this is not 8 GB evidence."
    echo "Reason: only ${AVAIL_GB} GB was available at start (free $FREE_GB + inactive $INACTIVE_GB + speculative $SPEC_GB + purgeable $PURGE_GB), below the model's published floor of ${MIN_FREE_GB} GB."
    echo "Mode that would have been used: $APP_MODE (the app's rule: low-RAM below 24 GB, Z_IMAGE_LOW_RAM_BELOW_BYTES)."
    echo "What to do: quit other apps (browsers are the usual cause) or restart the Mac, then run this again. Nothing was generated; this is not a result and must not be read as one."
    echo "===================== TO HERE ============================"
  } | tee "$OUT/NOT-RUN.txt"
  echo
  echo "Nothing was generated. Full detail: $OUT/host.txt" >&2
  exit 3
fi

if [ "$SMOKE" = 1 ] && [ "$ON_TARGET" != yes ]; then
  echo
  echo "SMOKE MODE on a $MEM_GB GB Mac (not the 8 GB target): this proves the harness only."
  echo "It is not a measurement of the 8 GB tier and must not be reported as one."
fi

# ── measurement ────────────────────────────────────────────────────────────────────
SWAP_LIMIT_GB="${ZIMAGE_MAX_SWAP_GB:-6}"   # abort a run that starts thrashing the SSD
WALL_LIMIT="${ZIMAGE_TIMEOUT:-1800}"       # 30 min per run, so nothing hangs forever

compressor_pages() {
  [ -n "$VMSTAT" ] || return 1
  vm_pages 'Pages occupied by compressor'
}

# The decoded pixels, not the file bytes: mflux writes PNG metadata (including when it ran), so
# two identical generations can differ as files. The decoded RGBA hash is the one that says
# "the same picture". If PIL is unavailable the file hash is used, labelled as such.
png_hash() {
  local png="$1" h=""
  if [ -n "$PY_IMG" ]; then
    h="$("$PY_IMG" - "$png" <<'PY' 2>/dev/null
import hashlib, sys
from PIL import Image
with Image.open(sys.argv[1]) as im:
    print(hashlib.sha256(im.convert("RGBA").tobytes()).hexdigest())
PY
)"
    [ -n "$h" ] && { printf 'sha256(decoded RGBA) %s\n' "$h"; return 0; }
  fi
  if [ -n "$SHASUM" ]; then
    h="$("$SHASUM" -a 256 "$png" 2>/dev/null | awk '{print $1}')"
    [ -n "$h" ] && { printf 'sha256(file bytes) %s\n' "$h"; return 0; }
  fi
  printf 'hash unavailable\n'
}

# Stop the generation itself, not just the `time` wrapper around it. The old wall-limit path
# killed `time` and left mflux running — an orphan that kept the machine's memory and would have
# been measured as part of the next run.
stop_mflux() {
  [ -n "$PKILL" ] || return 0
  "$PKILL" -TERM -f 'mflux-generate-z-image-turbo' 2>/dev/null
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    [ -n "$PGREP" ] || break
    "$PGREP" -f 'mflux-generate-z-image-turbo' >/dev/null 2>&1 || return 0
    sleep 0.5
  done
  "$PKILL" -KILL -f 'mflux-generate-z-image-turbo' 2>/dev/null
}
trap 'echo; echo "interrupted — stopping the generation"; stop_mflux; exit 130' INT TERM

printf 'label\tscene\tresolution\tconfig\tsteps\tresult\twall_s\tpeak_mlx\tmax_rss_gb\tpeak_footprint_gb\tpeak_footprint_bytes\tproc_swaps\tswap_before_mb\tswap_after_mb\tswap_peak_mb\tcompressor_delta_pages\tnote\n' >> "$RESULTS"

run() { # run <label> <scene> <resolution> <config> <steps> <prompt> [extra args...]
  local label="$1" scene="$2" res="$3" config="$4" steps="$5" prompt="$6"; shift 6
  local log="$LOGS/$label.log" png="$OUT/$label.png"
  local sw_before co_before sw_after co_after
  sw_before="$(swap_used_mb || true)"; co_before="$(compressor_pages || true)"

  echo
  echo "=== $label ($res, $config, $steps steps) ==="
  local start; start="$("$DATE" +%s)"

  # Sample swap while it runs, so an 8 GB machine's peak is recorded rather than only its
  # endpoints — the peak is the whole question at this tier. No `local` in here: this is a
  # subshell, not the function body. Both background jobs send their output to /dev/null, and
  # that is load-bearing rather than tidiness: killing the subshell leaves its `sleep` child
  # running, and a child that still holds this script's stdout keeps a pipe such as `| tail`
  # open until the sleep expires — so the run finished in 20 s and the command appeared to hang
  # for the full 30-minute wall limit.
  local peak_file="$LOGS/$label.swap-peak"; echo 0 > "$peak_file"
  local killer="" pid now watchdog
  ( while :; do
      sleep 3
      now="$(swap_used_mb || true)"
      [ -n "${now:-}" ] || continue
      if awk -v a="$now" -v b="$(cat "$peak_file")" 'BEGIN{exit !(a>b)}'; then
        echo "$now" > "$peak_file"
      fi
      if awk -v a="$now" -v l="$SWAP_LIMIT_GB" 'BEGIN{exit !(a > l*1024)}'; then
        echo "swap exceeded ${SWAP_LIMIT_GB} GB - stopping this run" >> "$log"
        stop_mflux
        break
      fi
    done ) >/dev/null 2>&1 & killer=$!

  if [ -n "$TIME" ]; then
    "$TIME" -l "$MFLUX" \
        --model "$PACK" \
        --prompt "$prompt" \
        --width "${res%x*}" --height "${res#*x}" \
        --steps "$steps" --seed 42 \
        --output "$png" "$@" >"$log" 2>&1 &
  else
    "$MFLUX" \
        --model "$PACK" \
        --prompt "$prompt" \
        --width "${res%x*}" --height "${res#*x}" \
        --steps "$steps" --seed 42 \
        --output "$png" "$@" >"$log" 2>&1 &
  fi
  pid=$!
  ( sleep "$WALL_LIMIT"; if kill -0 "$pid" 2>/dev/null; then echo "wall limit ${WALL_LIMIT}s reached - stopping" >> "$log"; stop_mflux; fi ) >/dev/null 2>&1 &
  watchdog=$!
  wait "$pid"; local rc=$?
  kill "$killer" "$watchdog" 2>/dev/null; wait "$killer" "$watchdog" 2>/dev/null

  # Nothing may be left running before the next run starts.
  if [ -n "$PGREP" ] && "$PGREP" -f 'mflux-generate-z-image-turbo' >/dev/null 2>&1; then
    stop_mflux
  fi

  local wall=$(( $("$DATE" +%s) - start ))
  sw_after="$(swap_used_mb || true)"; co_after="$(compressor_pages || true)"

  local peak_mlx rss foot foot_bytes pswaps png_fresh=0 png_mtime result note=""
  peak_mlx="$(awk -F': *' '/Peak MLX memory/ {v=$NF} END{if (v!="") print v}' "$log")"
  rss="$(awk '/maximum resident set size/ {v=$1} END{if (v!="") printf "%.2f", v/1073741824}' "$log")"
  foot_bytes="$(awk '/peak memory footprint/ {v=$1} END{if (v!="") print v}' "$log")"
  foot="$(awk -v b="${foot_bytes:-}" 'BEGIN{if (b!="") printf "%.2f", b/1073741824}')"
  pswaps="$(awk '/^ *[0-9]+ +swaps/ {v=$1} END{if (v!="") print v}' "$log")"

  # An image from an earlier run must not be counted as this run's output: rc=0 plus a file whose
  # mtime is older than this run is not a pass.
  if [ -n "$STAT" ] && [ -f "$png" ]; then
    png_mtime="$("$STAT" -f %m "$png" 2>/dev/null || echo 0)"
    [ "${png_mtime:-0}" -ge "$start" ] && png_fresh=1
  fi

  if [ "$rc" -eq 0 ] && [ "$png_fresh" -eq 1 ]; then
    result="OK"
  elif grep -q 'swap exceeded' "$log"; then
    result="ABORTED: swap limit"
  elif grep -q 'wall limit' "$log"; then
    result="ABORTED: wall limit"
  elif grep -qiE 'out of memory|OutOfMemory|MemoryError|bad_alloc|Impacting Interactivity|kIOGPUCommandBuffer' "$log"; then
    result="FAILED: out of memory / GPU abort"
    note="$(grep -iE 'out of memory|OutOfMemory|MemoryError|bad_alloc|Impacting Interactivity|kIOGPUCommandBuffer' "$log" | tail -1)"
  elif [ "$rc" -eq 137 ]; then
    result="FAILED: killed by the system (SIGKILL, likely memory)"
  elif [ "$rc" -eq 0 ]; then
    result="FAILED: no new image written"
  else
    result="FAILED: exit $rc"
    note="$(tail -1 "$log" 2>/dev/null)"
  fi
  note="$(printf '%s' "$note" | tr '\t\r\n' '   ' | cut -c1-160)"

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$label" "$scene" "$res" "$config" "$steps" "$result" "$wall" \
    "${peak_mlx:-?}" "${rss:-?}" "${foot:-?}" "${foot_bytes:-?}" "${pswaps:-?}" \
    "${sw_before:-?}" "${sw_after:-?}" "$(cat "$peak_file")" \
    "$(( ${co_after:-0} - ${co_before:-0} ))" "$note" >> "$RESULTS"

  echo "    $result   wall ${wall}s   peak MLX ${peak_mlx:-?}   footprint ${foot:-?} GB   swap peak $(cat "$peak_file") MB"
}

# ── the two owner scenes, verbatim from run-text-tests.sh so images compare ────────
SLEEP_PROMPT='YouTube sleep-story cover art, golden magical theme. Large elegant gold serif title text reading exactly "GOLDEN SLUMBER" across the top. A moonlit enchanted forest with a winding path, glowing golden fireflies and drifting sparkles, deep midnight-blue and gold palette, soft dreamy haze, cinematic, highly detailed.'
BOOK_PROMPT='Children'"'"'s storybook cover illustration with readable title text. The title "THE LITTLE STAR" in large playful rounded letters across the top, and the subtitle "A Bedtime Story" in smaller letters just below it. A cute sleepy star character resting on soft clouds, warm pastel colours, whimsical, gentle, clean composition.'

# ── the runs ───────────────────────────────────────────────────────────────────────
if [ "$SMOKE" = 1 ]; then
  # Proves the plumbing — pack discovery, engine discovery, `time -l` parsing, the swap sampler,
  # the TSV, the image hash — without spending half an hour of an 8 GB Mac's time. Small and few
  # steps on purpose: this says nothing about whether Z-Image fits, only that the harness works.
  # The output folder was already redirected to a temporary directory above, so a smoke check
  # never disturbs a real run.
  echo "SMOKE MODE: one 512x512, 2-step run into a temporary folder."
  run "smoke" smoke 512x512 "low-ram" 2 "$BOOK_PROMPT" --low-ram
  echo
  echo "Image hash (checks the hash helper too): $(png_hash "$OUT/smoke.png")"
  echo
  echo "Harness works if the row above says OK. Temporary output: $OUT"
  echo "This was a harness check, not a measurement — nothing is reported from it."
  exit 0
fi

# 1024x1024 is the primary question: it is the scene whose subtitle text the shipped FLUX.2 Klein
# 4-bit misspells, so it is the one worth having on the small machines. 1280x720 is the owner's
# 16:9 spec (ST-17) and the app's default shape.
run "01-1024-lowram"       storybook 1024x1024 "low-ram"                        9 "$BOOK_PROMPT"  --low-ram
run "02-1024-lowram-tiled" storybook 1024x1024 "low-ram + vae-tile 256"          9 "$BOOK_PROMPT"  --low-ram --vae-tile-size 256
run "03-720-lowram"        sleep     1280x720  "low-ram"                        9 "$SLEEP_PROMPT" --low-ram
# Deliberately the default settings. On 16 GB and up this is the fast path; on 8 GB it is
# expected to fail or thrash, and a recorded failure is the answer — it says the app must force
# --low-ram on this tier rather than merely suggest it.
run "04-1024-default"      storybook 1024x1024 "default (expected to fail)"      9 "$BOOK_PROMPT"

# ── the record ─────────────────────────────────────────────────────────────────────
echo
echo "────────────────────────────────────────────────────────────────────────"
if [ -n "$COLUMN" ]; then "$COLUMN" -t -s "$(printf '\t')" "$RESULTS"; else cat "$RESULTS"; fi
echo "────────────────────────────────────────────────────────────────────────"

tsv() { awk -F'\t' -v l="$1" -v c="$2" '$1==l {print $c; exit}' "$RESULTS"; }
is_ok() { [ "$1" = "OK" ]; }

r01="$(tsv 01-1024-lowram 6)"; r02="$(tsv 02-1024-lowram-tiled 6)"
r03="$(tsv 03-720-lowram 6)";  r04="$(tsv 04-1024-default 6)"

# The verdict is about the tier, so it is decided from the low-RAM rows only. An ABORTED run is
# explicitly not a failure: the script stopped it, and the tier is then neither proven nor
# disproven — saying "not qualified" there would blame the model for the harness's own cap.
sw_note=""
# With ZIMAGE_ALLOW_NON_TARGET=1 the numbers are real but they are not 8 GB evidence; the verdict
# must not claim otherwise.
if [ "$ON_TARGET" = yes ]; then
  host_phrase="on this 8 GB Mac"
else
  host_phrase="on this ${MEM_GB} GB Mac, which is NOT the 8 GB tier — not 8 GB evidence"
fi
if is_ok "$r01" && is_ok "$r03"; then
  if awk -v s="$(tsv 01-1024-lowram 15)" 'BEGIN{exit !(s+0 > 2048)}'; then
    sw_note=" It swapped heavily (peak $(tsv 01-1024-lowram 15) MB), so the tier is qualified, not comfortable."
  fi
  verdict="QUALIFIED — low-RAM mode completed at 1024x1024 and 1280x720 $host_phrase; it would have failed if a run had ended in an out-of-memory/GPU abort or if swap had crossed the ${SWAP_LIMIT_GB} GB cap (neither happened).${sw_note}"
elif is_ok "$r01"; then
  verdict="PARTIAL — 1024x1024 low-RAM completed but the 1280x720 run (03) ended \"$r03\"; the tier is not fully qualified at the owner's 16:9 shape."
elif is_ok "$r02" && ! is_ok "$r01"; then
  verdict="QUALIFIED ONLY WITH 256-PIXEL TILED VAE — the standard low-RAM run (01) ended \"$r01\" but the tiled run (02) completed; it fails on this tier without --vae-tile-size 256."
elif [ "$r01" != "${r01#ABORTED}" ]; then
  verdict="INCONCLUSIVE — the primary low-RAM run (01) was stopped by the harness ($r01), so the tier is neither proven nor disproven; re-run with ZIMAGE_MAX_SWAP_GB=10 (or a longer ZIMAGE_TIMEOUT) to finish it."
else
  reason01="$(tsv 01-1024-lowram 17)"
  [ -n "$reason01" ] && reason01=" Last log line: ${reason01:0:90}."
  verdict="NOT QUALIFIED — the primary low-RAM run (01) ended \"$r01\" (peak MLX $(tsv 01-1024-lowram 8), footprint $(tsv 01-1024-lowram 10) GB).$reason01 A run fails on this tier when memory runs out or the GPU aborts."
fi
case "$r04" in
  OK) default_line="run 04 (default mode) also completed — the app's low-RAM rule was not needed here, which contradicts the expected result and is worth a look." ;;
  *)  default_line="run 04 (default mode) $r04 — the app must keep forcing low-RAM on this tier." ;;
esac

img_hashes() { # one line per image that ran to completion
  local l
  for l in 01-1024-lowram 02-1024-lowram-tiled 03-720-lowram 04-1024-default; do
    [ "$(tsv "$l" 6)" = "OK" ] || continue
    printf '  %s  %s\n' "$l.png" "$(png_hash "$OUT/$l.png")"
  done
}

{
  echo "==================== COPY FROM HERE ===================="
  echo "8 GB TIER · Z-Image-Turbo 6B MLX 4-bit"
  echo "Machine: $CHIP · $MEM_GB GB unified memory · macOS $OSV"
  [ "$ON_TARGET" = yes ] || echo "WARNING: this Mac is not the 8 GB tier, so these numbers are real but are NOT 8 GB evidence."
  echo "Model: filipstrand/Z-Image-Turbo-mflux-4bit @ ${PACK_REV} (4-bit pack, $("$DU" -sk "$PACK" 2>/dev/null | awk '{printf "%.2f GB", $1*1024/1e9}') on disk)"
  echo "Engine: mflux $MFVER · $MFLUX"
  echo "Mode: runs 01-03 use low-ram; the app's own rule selects $APP_MODE on this Mac (low-RAM below 24 GB, Z_IMAGE_LOW_RAM_BELOW_BYTES); run 04 measures the default mode"
  echo "All runs: seed 42, 9 steps, wall clock includes model load. Peak MLX is mflux's own reported high-water in its own units; footprint is exact bytes from /usr/bin/time -l."
  echo "Memory at start: ${AVAIL_GB:-?} GB available of ${MEM_GB} GB, swap ${SWAP_START_MB:-?} MB used"
  printf '  %-21s %-10s %-5s %-20s %-7s %6s  %-9s %-11s %-22s %s\n' label resolution steps mode result wall peak_mlx footprint swap ""
  for l in 01-1024-lowram 02-1024-lowram-tiled 03-720-lowram 04-1024-default; do
    printf '  %-21s %-10s %-5s %-20s %-7s %5s s  %-9s %-11s %-22s %s\n' \
      "$l" "$(tsv "$l" 3)" "$(tsv "$l" 5)" "$(tsv "$l" 4)" "$(tsv "$l" 6)" "$(tsv "$l" 7)" \
      "$(tsv "$l" 8)" "$(tsv "$l" 10) GB" "swap $(tsv "$l" 13)->$(tsv "$l" 14) MB peak $(tsv "$l" 15)" \
      "swaps $(tsv "$l" 11)"
  done
  echo "Images:"
  img_hashes
  echo "Verdict: $verdict"
  echo "$default_line"
  echo "===================== TO HERE ============================"
} | tee "$OUT/VERDICT.txt"

echo
echo "Results: $RESULTS"
echo "Logs:    $LOGS"
echo "Images:  $OUT/*.png"
echo "Paste back the block above — it is also saved as $OUT/VERDICT.txt"
# `[ "$MFVER" != 0.20.* ]` would be a literal comparison and warn on a correct version; the
# glob has to be a case pattern.
case "$MFVER" in
  0.20.*) ;;
  *) echo "NOTE: the block records mflux $MFVER, not the qualified 0.20.0 — say so when sending it." ;;
esac
