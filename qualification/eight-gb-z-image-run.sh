#!/bin/bash
# Z-Image-Turbo on an 8 GB Mac — the one tier that is still unestablished.
#
#   bash eight-gb-z-image-run.sh [path-to-z-image-pack]
#
# The 32 GB numbers already recorded (qualification/z-image-turbo-2026-09-24.md) cannot answer
# this tier: on a big machine MLX's buffer cache inflates the peak into whatever RAM is free, so
# the 17.7-20.4 GB "default" footprint is an artefact of having room to grow. The only honest
# answer comes from a real 8 GiB M1/M2, which is what this script is for.
#
# It runs the two owner scenes at the settings most likely to fit, plus one deliberate attempt at
# the default settings so the record shows what does NOT work as well as what does. Every run is
# timed and measured with `time -l` in the same way as the 32 GB figures, so the two are
# comparable.
#
# Nothing is uploaded. Output lands in ~/Desktop/StudioHub Z-Image 8GB Run/.
#
# Close your other apps first. On 8 GB this is genuinely tight, and the point of the exercise is
# to find out how tight, so a busy machine would measure the wrong thing.
set -u

# ── where things are ───────────────────────────────────────────────────────────────
PACK="${1:-}"
if [ -z "$PACK" ]; then
  for candidate in \
    /Volumes/*/"Video Generation Kit/Studio Hub KH/optional-models/z-image-turbo-mflux-4bit" \
    "$HOME"/StudioHubModels/z-image-turbo-mflux-4bit/*/model \
    "$HOME"/.cache/huggingface/hub/models--filipstrand--Z-Image-Turbo-mflux-4bit/snapshots/*; do
    if [ -d "$candidate/transformer" ]; then PACK="$candidate"; break; fi
  done
fi
if [ -z "$PACK" ] || [ ! -d "$PACK/transformer" ]; then
  cat >&2 <<'EOF'
Could not find the Z-Image-Turbo pack.

Pass it explicitly, e.g.:
  bash eight-gb-z-image-run.sh "/Volumes/KH-STUDIO-ATLAS/Video Generation Kit/Studio Hub KH/optional-models/z-image-turbo-mflux-4bit"

It must be a folder containing transformer/, text_encoder/, tokenizer/ and vae/.
EOF
  exit 1
fi

OUT="$HOME/Desktop/StudioHub Z-Image 8GB Run"
LOGS="$OUT/logs"
mkdir -p "$LOGS"
RESULTS="$OUT/results.tsv"
: > "$RESULTS"

# ── mflux 0.20.0, the version this model was qualified on ──────────────────────────
export PATH="$HOME/.local/bin:$PATH"
if ! command -v mflux-generate-z-image-turbo >/dev/null 2>&1; then
  echo "mflux is not installed. Installing mflux==0.20.0 (the version Z-Image was qualified on)…"
  if command -v uv >/dev/null 2>&1; then
    uv tool install mflux==0.20.0 || { echo "install failed" >&2; exit 1; }
  elif command -v pipx >/dev/null 2>&1; then
    pipx install mflux==0.20.0 || { echo "install failed" >&2; exit 1; }
  else
    echo "Neither uv nor pipx is present. Install uv (https://docs.astral.sh/uv/) and re-run." >&2
    exit 1
  fi
  export PATH="$HOME/.local/bin:$PATH"
fi

# ── the machine, stated plainly in the record ──────────────────────────────────────
CHIP="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo unknown)"
MEM_GB="$(sysctl -n hw.memsize 2>/dev/null | awk '{printf "%.1f", $1/1073741824}')"
OSV="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
# Report the version actually installed. Claiming "0.20.0" because the command exists would make
# every number below incomparable with the 32 GB run while looking perfectly fine.
MFVER="$(uv tool list 2>/dev/null | awk '/^mflux /{print $2; exit}')"
[ -z "$MFVER" ] && MFVER="$(python3 -c 'import importlib.metadata as m; print(m.version("mflux"))' 2>/dev/null)"
[ -z "$MFVER" ] && MFVER="unknown"
{
  echo "Host: $CHIP · ${MEM_GB} GB · macOS $OSV · mflux ${MFVER#v}"
  echo "Pack: $PACK"
  echo "Pack bytes: $(du -sk "$PACK" 2>/dev/null | awk '{printf "%.2f GB", $1/1048576}')"
  echo "Date: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
} | tee "$OUT/host.txt"
case "${MFVER#v}" in
  0.20.*) ;;
  *) echo
     echo "WARNING: mflux ${MFVER#v} is installed, not the 0.20.0 this model was qualified on." >&2
     echo "         The timings below will not be comparable with the 32 GB record." >&2
     echo "         Fix with:  uv tool install --force mflux==0.20.0" >&2
     echo ;;
esac

# ── the two owner scenes, verbatim from run-text-tests.sh so images compare ────────
SLEEP_PROMPT='YouTube sleep-story cover art, golden magical theme. Large elegant gold serif title text reading exactly "GOLDEN SLUMBER" across the top. A moonlit enchanted forest with a winding path, glowing golden fireflies and drifting sparkles, deep midnight-blue and gold palette, soft dreamy haze, cinematic, highly detailed.'
BOOK_PROMPT='Children'"'"'s storybook cover illustration with readable title text. The title "THE LITTLE STAR" in large playful rounded letters across the top, and the subtitle "A Bedtime Story" in smaller letters just below it. A cute sleepy star character resting on soft clouds, warm pastel colours, whimsical, gentle, clean composition.'

# ── measurement ────────────────────────────────────────────────────────────────────
SWAP_LIMIT_GB="${ZIMAGE_MAX_SWAP_GB:-6}"   # abort a run that starts thrashing the SSD
WALL_LIMIT="${ZIMAGE_TIMEOUT:-1800}"       # 30 min per run, so nothing hangs forever
export PATH

swap_used_mb() { sysctl -n vm.swapusage 2>/dev/null | sed -E 's/.*used = ([0-9.]+)M.*/\1/'; }
compressor_pages() { vm_stat 2>/dev/null | awk -F: '/Pages occupied by compressor/ {gsub(/[^0-9]/,"",$2); print $2}'; }

printf 'label\tscene\tresolution\tconfig\tsteps\tresult\twall_s\tpeak_mlx\tmax_rss_gb\tpeak_footprint_gb\tproc_swaps\tswap_before_mb\tswap_after_mb\tswap_peak_mb\tcompressor_delta_pages\n' >> "$RESULTS"

run() { # run <label> <scene> <resolution> <config> <prompt> <extra args...>
  local label="$1" scene="$2" res="$3" config="$4" prompt="$5"; shift 5
  local log="$LOGS/$label.log"
  local sw_before co_before sw_after co_after
  sw_before="$(swap_used_mb)"; co_before="$(compressor_pages)"

  echo
  echo "=== $label ($res, $config) ==="
  local start; start="$(date +%s)"

  # Sample swap while it runs, so an 8 GB machine's peak is recorded rather than only its
  # endpoints - the peak is the whole question at this tier. No `local` in here: this is a
  # subshell, not the function body.
  local peak_file="$LOGS/$label.swap-peak" ; echo 0 > "$peak_file"
  local killer="" pid now
  ( while :; do
      sleep 3
      now="$(swap_used_mb)"
      [ "${now:-0}" != "" ] || continue
      if awk -v a="$now" -v b="$(cat "$peak_file")" 'BEGIN{exit !(a>b)}'; then
        echo "$now" > "$peak_file"
      fi
      if awk -v a="$now" -v l="$SWAP_LIMIT_GB" 'BEGIN{exit !(a > l*1024)}'; then
        echo "swap exceeded ${SWAP_LIMIT_GB} GB - stopping this run" >> "$log"
        pkill -f 'mflux-generate-z-image-turbo' 2>/dev/null
        break
      fi
    done ) & killer=$!

  /usr/bin/time -l mflux-generate-z-image-turbo \
      --model "$PACK" \
      --prompt "$prompt" \
      --width "${res%x*}" --height "${res#*x}" \
      --steps 9 --seed 42 \
      --output "$OUT/$label.png" "$@" >"$log" 2>&1 &
  pid=$!
  ( sleep "$WALL_LIMIT"; kill -0 "$pid" 2>/dev/null && { echo "wall limit ${WALL_LIMIT}s reached - stopping" >> "$log"; kill "$pid" 2>/dev/null; } ) &
  local watchdog=$!
  wait "$pid"; local rc=$?
  kill "$killer" "$watchdog" 2>/dev/null; wait "$killer" "$watchdog" 2>/dev/null

  local wall=$(( $(date +%s) - start ))
  sw_after="$(swap_used_mb)"; co_after="$(compressor_pages)"

  local peak_mlx rss foot pswaps result
  peak_mlx="$(grep -i 'Peak MLX memory' "$log" | tail -1 | sed -E 's/.*: *//')"
  rss="$(grep 'maximum resident set size' "$log" | tail -1 | awk '{printf "%.2f", $1/1073741824}')"
  foot="$(grep 'peak memory footprint' "$log" | tail -1 | awk '{printf "%.2f", $1/1073741824}')"
  pswaps="$(grep -E '^ *[0-9]+ +swaps' "$log" | tail -1 | awk '{print $1}')"

  if [ -f "$OUT/$label.png" ] && [ "$rc" -eq 0 ]; then
    result="OK"
  elif grep -qiE 'out of memory|OutOfMemory|Impacting Interactivity|kIOGPUCommandBuffer' "$log"; then
    result="FAILED: out of memory / GPU abort"
  elif grep -q 'swap exceeded' "$log"; then
    result="ABORTED: swap limit"
  elif grep -q 'wall limit' "$log"; then
    result="ABORTED: wall limit"
  else
    result="FAILED: exit $rc"
  fi

  printf '%s\t%s\t%s\t%s\t9\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$label" "$scene" "$res" "$config" "$result" "$wall" \
    "${peak_mlx:-?}" "${rss:-?}" "${foot:-?}" "${pswaps:-?}" \
    "${sw_before:-?}" "${sw_after:-?}" "$(cat "$peak_file")" \
    "$(( ${co_after:-0} - ${co_before:-0} ))" >> "$RESULTS"

  echo "    $result   wall ${wall}s   peak MLX ${peak_mlx:-?}   footprint ${foot:-?} GB   swap peak $(cat "$peak_file") MB"
}

# ── the runs ───────────────────────────────────────────────────────────────────────
# 1024x1024 is the primary question: it is the scene whose subtitle text the shipped FLUX.2
# Klein misspells, so it is the one worth having on the small machines.
if [ "${ZIMAGE_SMOKE:-0}" = "1" ]; then
  # Proves the plumbing - pack discovery, mflux, `time -l` parsing, the swap sampler, the TSV -
  # without spending 20 minutes of an 8 GB Mac's time. Small and few steps on purpose: this says
  # nothing about whether Z-Image fits, only that the harness works.
  echo "SMOKE MODE: one 512x512, 2-step run into a temporary folder."
  OUT="$(mktemp -d)/Z-Image Smoke"; LOGS="$OUT/logs"; mkdir -p "$LOGS"; RESULTS="$OUT/results.tsv"
  : > "$RESULTS"
  printf 'label\tscene\tresolution\tconfig\tsteps\tresult\twall_s\tpeak_mlx\tmax_rss_gb\tpeak_footprint_gb\tproc_swaps\tswap_before_mb\tswap_after_mb\tswap_peak_mb\tcompressor_delta_pages\n' >> "$RESULTS"
  run "smoke" smoke 512x512 "low-ram, 2 steps" "$BOOK_PROMPT" --low-ram --steps 2
  echo
  echo "Harness works if the row above says OK. Temporary output: $OUT"
  exit 0
fi

run "01-1024-lowram"        storybook 1024x1024 "low-ram"                    "$BOOK_PROMPT"  --low-ram
run "02-1024-lowram-tiled"  storybook 1024x1024 "low-ram + vae-tile 256"      "$BOOK_PROMPT"  --low-ram --vae-tile-size 256
run "03-720-lowram"         sleep     1280x720  "low-ram"                    "$SLEEP_PROMPT" --low-ram
# Deliberately the default settings. On 16 GB and up this is the fast path; on 8 GB it is
# expected to fail, and a recorded failure is the answer - it says the app must force --low-ram
# on this tier rather than merely suggest it.
run "04-1024-default"       storybook 1024x1024 "default (expected to fail)"  "$BOOK_PROMPT"

# ── summary ────────────────────────────────────────────────────────────────────────
echo
echo "────────────────────────────────────────────────────────────────────────"
column -t -s "$(printf '\t')" "$RESULTS" 2>/dev/null || cat "$RESULTS"
echo "────────────────────────────────────────────────────────────────────────"
echo
echo "Results: $RESULTS"
echo "Logs:    $LOGS"
echo "Images:  $OUT/*.png"
echo
echo "Read the 01/02/03 rows for the verdict, and 04 for what does not fit."
echo "If 01 fails but 02 succeeds, the app must use tiled VAE decode on this tier."
