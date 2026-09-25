#!/bin/bash
# Qwen3.5-9B 4-bit on the 16 GB and 8 GB tiers — the throughput measurement ST-14 still owes.
#
#   bash chat-tier-run.sh
#
# Nothing to edit, nothing to upload. The script finds the app (installed, or on a mounted kit
# drive), finds the shipped chat model through the bundle's own catalog, checks that this machine
# is a tier worth measuring, runs the packaged Chat worker against the real weights and prints one
# short block to paste back. Output lands in ~/Desktop/StudioHub Chat Tier Run/ as VERDICT.txt,
# results.tsv, host.txt and logs/.
#
# The question: does mlx-community/Qwen3.5-9B-4bit (uniform 4-bit MLX, 5.98 GB) do useful work on
# the owner's 16 GB Macs, and what does an 8 GB Mac actually do — the model's own manifest
# qualifies it at 16 GB, and nothing has been measured below 32 GB. All numbers in one block are
# from THIS host: the script does not estimate any other tier, because MLX's buffer cache grows
# into whatever RAM is free, so a 32 GB peak is partly a property of the 32 GB host.
#
# It refuses, rather than guessing, and says which check failed with the measured reason:
#   * this is not an 8 or 16 GB Mac    (a bigger or smaller host answers a different question)
#   * no packaged app was found        (needs the installed app, or a mounted kit drive)
#   * the shipped chat model is absent (or incomplete: config/weights/tokenizer)
#   * less memory is available than the checkpoint needs before macOS starts swapping it
# A run stopped by the swap cap or the wall limit is recorded ABORTED and the verdict is
# INCONCLUSIVE, never "failed": the harness stopped it, so the tier is neither proven nor
# disproven. An out-of-memory abort is a real answer and is recorded as such.
#
# Close your other apps first (browsers first), plug in power, and leave the Mac alone while it
# runs. On 8 GB this is genuinely tight, and the point of the exercise is to find out how tight.
#
# Knobs (the defaults are right for the one-command run; all are optional):
#   CHAT_SMOKE=1                harness self-check only: loads the model, one 8-token completion
#                               into a temporary folder, no verdict. Never touches the record.
#   CHAT_ALLOW_NON_TARGET=1     run on a Mac that is not the 8/16 GB tier anyway (the block then
#                               says the numbers are real but are not 8/16 GB evidence).
#   CHAT_IGNORE_FREE_FLOOR=1    attempt the run with less memory available than the weights need
#                               (on an 8 GB Mac this is the override described in the README).
#   CHAT_HEADROOM_GB=0.5        working room added to the checkpoint's own bytes for the floor.
#   CHAT_MAX_TOKENS=256         completion length of the measured runs.
#   CHAT_PROMPT="..."           use this prompt instead of the built-in one.
#   CHAT_MAX_SWAP_GB=6          stop a run whose system swap passes this.
#   CHAT_TIMEOUT=900            stop a single request after this many seconds.
#   CHAT_APP=...                use this Studio Hub.app bundle.
#   CHAT_MODEL=...              use this model folder (must contain config.json + weights).
#   CHAT_MODELS_DIR=...         use this model library root (…/<package>/<revision>/model).
#   CHAT_OUT=...                write the record somewhere other than ~/Desktop.
set -u

# A double-clicked or pasted script can arrive with a minimal PATH. Put the system directories
# back first, and keep whatever PATH came in after them so a user-installed tool is still found.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

# ── tools, by absolute path ────────────────────────────────────────────────────────
# `sysctl` and `vm_stat` matter most: neither is on every PATH, and a bare `sysctl` that silently
# failed once made a swap sampler report a peak of 0 MB and the host line read "unknown" — a zero
# that looks like a reading. Where a tool is missing, the record says so and every figure that
# needed it is written as "?" instead of 0.
ABS() { local p; for p in "$@"; do [ -x "$p" ] && { printf '%s\n' "$p"; return 0; }; done; return 1; }
SYSCTL="$(ABS /usr/sbin/sysctl /usr/bin/sysctl || true)"
VMSTAT="$(ABS /usr/bin/vm_stat || true)"
MKTEMP="$(ABS /usr/bin/mktemp || true)"
CURL="$(ABS /usr/bin/curl || true)"
DU="$(ABS /usr/bin/du || true)"
DATE="$(ABS /bin/date /usr/bin/date || true)"
COLUMN="$(ABS /usr/bin/column || true)"
SW_VERS="$(ABS /usr/bin/sw_vers || true)"
SYSPROF="$(ABS /usr/sbin/system_profiler /usr/bin/system_profiler || true)"
[ -n "$DATE" ] || { echo "no date(1) at /bin/date" >&2; exit 1; }
[ -n "$CURL" ] || { echo "no curl(1) at /usr/bin/curl; this script needs it to talk to the worker" >&2; exit 1; }

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
  MEM_GB="$("$SYSPROF" SPHardwareDataType 2>/dev/null | awk -F': ' '/^ *Memory:/{print $2; exit}')"
  [ -n "$MEM_GB" ] || MEM_GB="unknown"
fi
[ -n "$CHIP" ] || CHIP="$("$SYSPROF" SPHardwareDataType 2>/dev/null | awk -F': ' '/^ *Chip:/{print $2; exit}')"
[ -n "$CHIP" ] || CHIP="${MODEL_ID:-unknown}"
OSV="$("$SW_VERS" -productVersion 2>/dev/null || echo unknown)"

# Which tier is this? 8 GiB = 8589934592 bytes, 16 GiB = 17179869184. The boundaries sit between
# the real tiers (9 GiB, 17 GiB) so a real machine cannot be misclassified, and a host whose
# memory could not be read is "unknown" — a refusal in a real run, not a silent pass.
TIER=unknown; ON_TARGET=no
if [ -n "$MEM_BYTES" ]; then
  if [ "$MEM_BYTES" -le $((9 * 1024 * 1024 * 1024)) ]; then TIER="8 GB"; ON_TARGET=yes
  elif [ "$MEM_BYTES" -le $((17 * 1024 * 1024 * 1024)) ]; then TIER="16 GB"; ON_TARGET=yes
  else TIER="$(awk -v g="$MEM_GB" 'BEGIN{printf "%.0f GB", g+0}')"; fi
elif [ "$MEM_GB" != unknown ]; then
  TIER="$(awk -v g="$MEM_GB" 'BEGIN{print (g+0 <= 9) ? "8 GB" : ((g+0 <= 17) ? "16 GB" : "other")}')"
  [ "$TIER" != other ] && ON_TARGET=yes || TIER="${MEM_GB} GB"
fi
# How this host is named in a block: a non-target host must not be labelled with a tier it is not.
case "$TIER" in
  "8 GB"|"16 GB") TIER_LABEL="$TIER" ;;
  *) TIER_LABEL="${MEM_GB} GB (NOT an 8/16 GB tier)" ;;
esac

# ── the guards, before anything is created ─────────────────────────────────────────
# A refusal must leave no folder and no results file behind, or a refused run could be mistaken
# for a run that happened and produced nothing.
if [ "${CHAT_SMOKE:-0}" != "1" ] && [ "$ON_TARGET" != yes ] && [ "${CHAT_ALLOW_NON_TARGET:-0}" != 1 ]; then
  cat >&2 <<EOF
REFUSING TO RUN: this script measures the 8 GB and 16 GB tiers, and this Mac reports
${MEM_GB} GB of unified memory. On a bigger host MLX grows its buffer cache into the free RAM,
so the peak measured here would be a property of this host, not of either tier.

  * Run it on a 16 GB Mac (the tier in question), or on an 8 GB Mac.
  * Harness self-check on this Mac instead:   CHAT_SMOKE=1 bash "$0"
  * Run anyway, accepting the numbers are NOT 8/16 GB evidence:
        CHAT_ALLOW_NON_TARGET=1 bash "$0"

Nothing was run and no record was written.
EOF
  exit 4
fi

# ── the packaged app, and its runtime ──────────────────────────────────────────────
# The app carries the chat worker, its runtime (mlx-lm) and the model catalog, so a Mac with the
# kit's app installed — or the kit drive itself — needs no network and no Python of its own.
APP=""
app_ok() { [ -f "$1/Contents/Resources/chat_worker.py" ] \
  && [ -f "$1/Contents/Resources/components/chat/app/backend/main.py" ] \
  && [ -f "$1/Contents/Resources/release-manifest.json" ]; }
for candidate in \
  "${CHAT_APP:-}" \
  /Applications/"Studio Hub.app" \
  "$HOME"/Applications/"Studio Hub.app" \
  /Volumes/*/*/"Studio Hub.app" \
  /Volumes/*/*/*/"Studio Hub.app" \
  /Volumes/*/*/*/*/"Studio Hub.app"; do
  [ -n "$candidate" ] && app_ok "$candidate" && { APP="$candidate"; break; }
done
if [ -z "$APP" ]; then
  cat >&2 <<'EOF'
REFUSING TO RUN: no packaged Studio Hub app was found, and the Chat lane needs the app's own
worker, runtime and model catalog.

  * Install the kit's app (drag "Studio Hub.app" to Applications), or plug in a kit drive and
    leave it mounted — this script looks for Studio Hub.app on every mounted volume too.
  * Or name a bundle explicitly:   CHAT_APP="/path/to/Studio Hub.app" bash "$0"

Nothing was run and no record was written.
EOF
  exit 5
fi
RES="$APP/Contents/Resources"
PY="$RES/runtimes/chat/bin/python3"
[ -x "$PY" ] || PY="$RES/runtimes/voice/bin/python3"
if [ ! -x "$PY" ]; then
  echo "REFUSING TO RUN: the app at $APP has no bundled Python runtime." >&2
  echo "That copy is incomplete; copy it again. Nothing was run and no record was written." >&2
  exit 5
fi

# The one chat manifest the bundle ships, found by engine rather than by file name: the worker
# serves exactly one model and refuses a bundle that carries none or several, so this mirrors
# that contract instead of hardcoding a package id.
MANIFEST="$("$PY" - "$RES/model-manifests" <<'PY' 2>/dev/null
import json, pathlib, sys
found = []
for path in sorted(pathlib.Path(sys.argv[1]).glob("*.json")):
    try:
        value = json.loads(path.read_text())
    except Exception:
        continue
    if isinstance(value, dict) and value.get("engine") == "chat-mlx":
        found.append(str(path))
print(found[0] if len(found) == 1 else "")
PY
)"
if [ -z "$MANIFEST" ]; then
  echo "REFUSING TO RUN: the app at $APP ships no single chat-mlx model manifest." >&2
  echo "Nothing was run and no record was written." >&2
  exit 5
fi
manifest_field() { "$PY" -c "
import json,sys
d=json.load(open(sys.argv[1]))
value=d
for key in sys.argv[2].split('.'):
    value = value.get(key) if isinstance(value, dict) else None
print('' if value is None else value)
" "$MANIFEST" "$1" 2>/dev/null || true; }
PKG="$(manifest_field package_id)"
REV="$(manifest_field revision)"
MODEL_ID="$(manifest_field model_id)"
QUAL_GB="$(manifest_field qualification.minimum_unified_memory_gb)"
[ -n "$QUAL_GB" ] || QUAL_GB="$(manifest_field qualification.recommended_unified_memory_gb)"
MODEL_BYTES="$("$PY" - "$MANIFEST" <<'PY' 2>/dev/null
import json, sys
d = json.load(open(sys.argv[1]))
print(sum(int(f.get("size") or 0) for f in d.get("files") or []))
PY
)"
case "$MODEL_BYTES" in ''|*[!0-9]*) MODEL_BYTES="" ;; esac
APP_VERSION="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('version') or '?')" \
  "$RES/release-manifest.json" 2>/dev/null || echo '?')"
APP_COMMIT="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('implementation_commit') or '?')" \
  "$RES/release-manifest.json" 2>/dev/null || echo '?')"

# ── the model, in the library layout the worker itself uses ────────────────────────
# <library>/<package_id>/<revision>/model. The library is the Mac's own copy first (faster, and
# it does not depend on a drive staying mounted), then any mounted kit drive.
model_dir_ok() { # the engine's own completeness rule
  [ -d "$1" ] && [ -f "$1/config.json" ] && [ -f "$1/tokenizer.json" -o -f "$1/vocab.json" \
    -o -f "$1/tokenizer_config.json" ] || return 1
  local w; for w in "$1"/*.safetensors; do [ -f "$w" ] && return 0; done
  return 1
}
MODELS_DIR=""
if [ -n "${CHAT_MODEL:-}" ]; then
  if ! model_dir_ok "$CHAT_MODEL"; then
    echo "REFUSING TO RUN: CHAT_MODEL=$CHAT_MODEL is not a complete model folder" >&2
    echo "(it must contain config.json, the safetensors weights and a tokenizer)." >&2
    echo "Nothing was run and no record was written." >&2
    exit 6
  fi
  MODELS_DIR="$(dirname "$(dirname "$(dirname "$CHAT_MODEL")")")"
fi
if [ -z "$MODELS_DIR" ]; then
  for library in \
    "${CHAT_MODELS_DIR:-}" \
    "$HOME/StudioHubModels" \
    /Volumes/*/*/*/model-library \
    /Volumes/*/*/model-library \
    /Volumes/*/model-library; do
    [ -n "$library" ] || continue
    if model_dir_ok "$library/$PKG/$REV/model"; then MODELS_DIR="$library"; break; fi
  done
fi
if [ -z "$MODELS_DIR" ]; then
  cat >&2 <<EOF
REFUSING TO RUN: the shipped chat model was not found.

It belongs at:  <library>/$PKG/$REV/model
Looked in:      ~/StudioHubModels, and model-library/ on every mounted volume.

  * Plug in the kit drive (it carries the model), or install the model from the app's Models page.
  * Or name the folder explicitly:
        CHAT_MODEL="/path/to/$PKG/$REV/model" bash "$0"

Nothing was run and no record was written.
EOF
  exit 6
fi
MODEL_DIR="$MODELS_DIR/$PKG/$REV/model"
MODEL_ON_DISK="$("$DU" -sk "$MODEL_DIR" 2>/dev/null | awk '{printf "%.2f", $1*1024/1e9}')"
MODEL_FILES="$(find "$MODEL_DIR" -type f 2>/dev/null | wc -l | tr -d ' ')"

# ── free memory at start ───────────────────────────────────────────────────────────
# vm_stat is the only honest source: "free" alone is misleading on macOS because inactive,
# speculative and purgeable pages are reclaimable. Available = free + inactive + speculative +
# purgeable, which is what the model has to work with before macOS compresses and swaps. The floor
# is the checkpoint's own bytes (the app's manifest states them) plus a small working margin, and
# it is compared in decimal GB, exactly as the Hub's own governor does (components/hub/backend/
# resources.py: vm.available / 1e9).
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
swap_used_mb >/dev/null 2>&1 && SWAP_START_MB="$(swap_used_mb)" || SWAP_START_MB="?"

HEADROOM_GB="${CHAT_HEADROOM_GB:-0.5}"
if [ -n "$MODEL_BYTES" ]; then
  FLOOR_GB="$(awk -v b="$MODEL_BYTES" -v h="$HEADROOM_GB" 'BEGIN{printf "%.2f", b/1e9 + h}')"
else
  FLOOR_GB=""
fi
if [ "${CHAT_SMOKE:-0}" != "1" ] && [ -n "$AVAIL_BYTES" ] && [ -n "$FLOOR_GB" ] \
   && [ "${CHAT_IGNORE_FREE_FLOOR:-0}" != 1 ] && [ "$TIER" != "8 GB" ]; then
  if awk -v b="$AVAIL_BYTES" -v f="$FLOOR_GB" 'BEGIN{exit !(b < f*1e9)}'; then
    # The refusal is printed as the same one block, to stdout only: on an 8 GB Mac this is the
    # most likely outcome, and the owner should be able to send it back without editing anything.
    # Nothing is created — a refusal is not a result and must not leave a record that looks like
    # one. The header says NOT RUN so a reader cannot mistake it for a measurement.
    cat <<EOF
==================== COPY FROM HERE ====================
NOT RUN · ${TIER_LABEL} · Qwen3.5-9B 4-bit (mlx-community/Qwen3.5-9B-4bit, uniform 4-bit MLX) — a refusal, not a measurement
Machine: $CHIP · $MEM_GB GB unified memory · macOS $OSV · ${MODEL_ID:-unknown}
App: Studio Hub $APP_VERSION ($APP_COMMIT)
Model: $MODEL_ID (uniform 4-bit MLX) @ $REV · ${MODEL_ON_DISK:-?} GB on disk, ${MODEL_FILES:-?} file(s)
Manifest qualifies it at: ${QUAL_GB:-none published} GB (qualification.minimum_unified_memory_gb)
Available memory at start: ${AVAIL_GB} GB (free $FREE_GB + inactive $INACTIVE_GB + speculative $SPEC_GB + purgeable $PURGE_GB), swap ${SWAP_START_MB:-?} MB used
Floor needed: ${FLOOR_GB} GB (the checkpoint's own $(awk -v b="$MODEL_BYTES" 'BEGIN{printf "%.2f", b/1e9}') GB + ${HEADROOM_GB} GB working room)
Reason: below the floor macOS swaps the load itself, so a run would measure the SSD, not the tier.
What to do: quit other apps (browsers are the usual cause) or restart the Mac, then run again.
            To see what this Mac does below the floor anyway:
                CHAT_IGNORE_FREE_FLOOR=1 bash chat-tier-run.sh
Nothing was run and no record was written. This refusal is not a throughput result.
===================== TO HERE ============================
EOF
    echo >&2
    echo "REFUSING TO RUN: ${AVAIL_GB} GB available is below the ${FLOOR_GB} GB floor." >&2
    echo "The block above is on standard output, for sending back. Nothing was run." >&2
    exit 3
  fi
fi
# ── the 8 GB tier is a documented exclusion, refused here and not probed ─────────────
# This model is not run on an 8 GB Mac. The owner tried it, it does not run there, and probing it
# again costs up to CHAT_TIMEOUT (900 s by default) per attempt plus the SSD traffic of a load that
# swaps — for an answer already known. So the refusal is immediate and says so, rather than leaving
# a gate that the tier might occasionally pass and then spend fifteen minutes failing.
#
# The gate this replaces checked only *available* memory, which on an 8 GB Mac is essentially never
# 6.48 GB free — so it refused for the wrong reason ("your Mac is busy") when the real answer is
# that the tier is excluded. A refusal that names the wrong cause sends the owner off to quit apps
# for nothing.
if [ "${CHAT_SMOKE:-0}" != "1" ] && [ "$TIER" = "8 GB" ] \
   && [ "${CHAT_ALLOW_EIGHT_GB:-0}" != 1 ]; then
  cat <<EOF
==================== COPY FROM HERE ====================
NOT RUN · 8 GB · Qwen3.5-9B 4-bit (mlx-community/Qwen3.5-9B-4bit, uniform 4-bit MLX) — a documented exclusion, not a measurement
Machine: $CHIP · $MEM_GB GB unified memory · macOS $OSV · ${MODEL_ID:-unknown}
App: Studio Hub $APP_VERSION ($APP_COMMIT)
Model: $MODEL_ID (uniform 4-bit MLX) @ $REV · ${MODEL_ON_DISK:-?} GB on disk, ${MODEL_FILES:-?} file(s)
Manifest qualifies it at: ${QUAL_GB:-none published} GB (qualification.minimum_unified_memory_gb)
Why this is refused outright: the weights are 5.98 GB and the load needs working room on top, so on
  an 8 GB Mac this is not a tight fit to be measured — it does not run. The owner tried it on his
  8 GB Mac; it is excluded there by his decision, and an attempt would spend up to ${CHAT_TIMEOUT:-900} s
  and a swapping model load to rediscover that.
What this Mac should run instead: the 8 GB tier runs the Voice, Subtitle and Image models this kit
  carries for it. Chat's job on your fleet is the 16 GB and larger Macs, where it is measured.
Nothing was run and no record was written. This refusal is not a throughput result.
If you genuinely want to watch it fail anyway, and you accept the time:
    CHAT_ALLOW_EIGHT_GB=1 CHAT_IGNORE_FREE_FLOOR=1 bash chat-tier-run.sh
===================== TO HERE ============================
EOF
  echo >&2
  echo "REFUSING TO RUN: Qwen3.5-9B 4-bit is excluded on the 8 GB tier — it does not run there." >&2
  echo "The block above is on standard output, for sending back. Nothing was run." >&2
  exit 3
fi

# ── where the record goes ──────────────────────────────────────────────────────────
# Smoke mode is decided here, before anything is created: it exists to prove the harness cheaply,
# so it must not touch the real output folder at all.
if [ "${CHAT_SMOKE:-0}" = "1" ]; then
  SMOKE=1
  if [ -n "$MKTEMP" ]; then SMOKE_TMP="$("$MKTEMP" -d)"; else SMOKE_TMP="${TMPDIR:-/tmp}/chat-smoke-$$"; mkdir -p "$SMOKE_TMP"; fi
  OUT="$SMOKE_TMP/Chat Smoke"
else
  SMOKE=0
  OUT="${CHAT_OUT:-$HOME/Desktop/StudioHub Chat Tier Run}"
fi
LOGS="$OUT/logs"
mkdir -p "$LOGS" || { echo "cannot create $LOGS" >&2; exit 1; }
RESULTS="$OUT/results.tsv"
if [ -s "$RESULTS" ]; then mv "$RESULTS" "$OUT/results-$(date -u '+%Y%m%dT%H%M%SZ').tsv"; fi
: > "$RESULTS"

# ── the worker, from the bundle, on a private loopback port ────────────────────────
DATA="$OUT/run-data"
mkdir -p "$DATA" && chmod 700 "$DATA" || { echo "cannot create $DATA" >&2; exit 1; }
TOKEN_FILE="$DATA/hub-token"
READY="$DATA/chat-ready.json"
WORKER_LOG="$LOGS/worker.log"
if [ -f "$TOKEN_FILE" ]; then :; else
  "$PY" -c "import secrets;print(secrets.token_urlsafe(32))" > "$TOKEN_FILE" 2>/dev/null \
    || { echo "cannot create the worker token" >&2; exit 1; }
fi
chmod 600 "$TOKEN_FILE"
TOKEN="$(cat "$TOKEN_FILE")"

WPID=""
stop_worker() {
  [ -n "$WPID" ] || return 0
  kill "$WPID" 2>/dev/null
  local i
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    kill -0 "$WPID" 2>/dev/null || { WPID=""; return 0; }
    sleep 0.5
  done
  kill -9 "$WPID" 2>/dev/null
  WPID=""
}
trap 'echo; echo "interrupted — stopping the worker"; stop_worker; exit 130' INT TERM

"$PY" -I -B "$RES/chat_worker.py" --data-dir "$DATA" --models-dir "$MODELS_DIR" \
  --ready-file "$READY" --hub-token-file "$TOKEN_FILE" --port 0 --offline \
  >"$WORKER_LOG" 2>&1 &
WPID=$!
for _ in $(seq 1 120); do
  [ -f "$READY" ] && break
  kill -0 "$WPID" 2>/dev/null || break
  sleep 1
done
if [ ! -f "$READY" ]; then
  echo "The packaged Chat worker did not become ready. Its log:" >&2
  tail -20 "$WORKER_LOG" >&2
  stop_worker
  { echo "==================== COPY FROM HERE ===================="
    echo "CHAT TIER RUN — WORKER DID NOT START (not a tier result)"
    echo "Machine: $CHIP · $MEM_GB GB unified memory · macOS $OSV"
    echo "App: Studio Hub $APP_VERSION ($APP_COMMIT) · $APP"
    echo "Model: $MODEL_ID (uniform 4-bit MLX) @ $REV"
    echo "The worker exited or never wrote its ready file; last log line:"
    tail -1 "$WORKER_LOG" 2>/dev/null
    echo "===================== TO HERE ============================"
  } | tee "$OUT/VERDICT.txt"
  exit 1
fi
URL="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['url'])" "$READY" 2>/dev/null)"
if [ -z "$URL" ]; then
  echo "The worker wrote an unreadable ready file at $READY" >&2
  stop_worker; exit 1
fi

# The worker's own view of this host and this model, before anything is generated.
worker_get() { # worker_get <path> <out-file>
  "$CURL" -sS --max-time 30 -H "Authorization: Bearer $TOKEN" "$URL$1" -o "$2" 2>/dev/null
}
worker_get /api/catalog "$OUT/chat-catalog.json"
CATALOG_LINE="$("$PY" - "$OUT/chat-catalog.json" <<'PY' 2>/dev/null
import json, sys
try:
    m = (json.load(open(sys.argv[1])).get("models") or [{}])[0]
except Exception:
    m = {}
print("|".join(str(m.get(k)) for k in
      ("label", "repo", "model_revision", "qualified_minimum_memory_gb", "memory_eligible",
       "available", "context_window_tokens", "max_output_tokens")))
warning = (m.get("memory_warning") or "").strip()
print(warning)
PY
)"
CAT_LABEL="$(printf '%s\n' "$CATALOG_LINE" | sed -n 1p | awk -F'|' '{print $1}')"
CAT_TIER="$(printf '%s\n' "$CATALOG_LINE" | sed -n 1p | awk -F'|' '{print $4}')"
CAT_ELIGIBLE="$(printf '%s\n' "$CATALOG_LINE" | sed -n 1p | awk -F'|' '{print $5}')"
CAT_AVAILABLE="$(printf '%s\n' "$CATALOG_LINE" | sed -n 1p | awk -F'|' '{print $6}')"
CAT_CONTEXT="$(printf '%s\n' "$CATALOG_LINE" | sed -n 1p | awk -F'|' '{print $7}')"
CAT_MAXOUT="$(printf '%s\n' "$CATALOG_LINE" | sed -n 1p | awk -F'|' '{print $8}')"
MEMORY_WARNING="$(printf '%s\n' "$CATALOG_LINE" | sed -n 2p)"

{
  echo "Host: $CHIP · $MEM_GB GB · macOS $OSV · model ${MODEL_ID:-unknown} · tier: $TIER_LABEL"
  echo "App: Studio Hub $APP_VERSION ($APP_COMMIT) · $APP"
  echo "Worker: $RES/chat_worker.py · runtime $PY"
  echo "Model: $MODEL_ID (uniform 4-bit MLX) @ $REV"
  echo "Model path: $MODEL_DIR"
  echo "Model size: ${MODEL_ON_DISK:-?} GB on disk, ${MODEL_FILES:-?} file(s)"
  echo "Manifest: $MANIFEST"
  echo "Published qualified tier: ${QUAL_GB:-none published} GB (qualification.minimum_unified_memory_gb)"
  echo "Worker catalog: label=$CAT_LABEL ready=$CAT_AVAILABLE host_eligible=$CAT_ELIGIBLE tier_published=$CAT_TIER context=$CAT_CONTEXT max_output=$CAT_MAXOUT"
  echo "Memory at start: ${AVAIL_GB:-?} GB available (free $FREE_GB + inactive $INACTIVE_GB + speculative $SPEC_GB + purgeable $PURGE_GB), wired $WIRED_GB GB, compressor $COMP_GB GB, swap used ${SWAP_START_MB:-?} MB"
  echo "Free-memory floor: ${FLOOR_GB:-none} GB (the checkpoint's own $( [ -n "$MODEL_BYTES" ] && awk -v b="$MODEL_BYTES" 'BEGIN{printf "%.2f", b/1e9}' || echo '?' ) GB + ${HEADROOM_GB} GB headroom)"
  echo "Date: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
} | tee "$OUT/host.txt"
[ -n "$MEMORY_WARNING" ] && { echo "Worker memory warning: $MEMORY_WARNING" | tee -a "$OUT/host.txt"; }

if [ -z "$VMSTAT" ]; then
  echo "WARNING: vm_stat unavailable — free memory and compressor figures are NOT readings." >&2
fi

if [ "$SMOKE" = 1 ] && [ "$ON_TARGET" != yes ]; then
  echo
  echo "SMOKE MODE on a $MEM_GB GB Mac (not an 8/16 GB target): this proves the harness only."
  echo "It is not a measurement of either tier and must not be reported as one."
fi

# ── measurement ────────────────────────────────────────────────────────────────────
SWAP_LIMIT_GB="${CHAT_MAX_SWAP_GB:-6}"    # abort a run that starts thrashing the SSD
WALL_LIMIT="${CHAT_TIMEOUT:-900}"         # one request; a cold 256-token run is minutes, not hours
MAX_TOKENS="${CHAT_MAX_TOKENS:-256}"
PROMPT="${CHAT_PROMPT:-Write a short story about a lighthouse keeper who finds a message in a bottle.}"

compressor_pages() { [ -n "$VMSTAT" ] || return 1; vm_pages 'Pages occupied by compressor'; }
fact() { awk -F= -v k="$2" '$1==k{sub(/^[^=]*=/,"");print;exit}' "$1" 2>/dev/null; }
peak_swap() { # the sampled swap peak, or "?" when no sample was ever taken
  local value; value="$(cat "$1" 2>/dev/null)"
  if [ -n "$value" ]; then printf '%s' "$value"; else printf '?'; fi
}

# Pin the model resident: the shipped default is `immediate` (release within one 5 s tick), which
# would make the second run a cold load and the two runs incomparable. State it in the record.
"$CURL" -sS --max-time 30 -X PUT "$URL/api/memory-policy" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"mode":"performance"}' -o "$LOGS/memory-policy.json" 2>/dev/null
POLICY_MODE="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('mode'))" \
  "$LOGS/memory-policy.json" 2>/dev/null || echo '?')"

diagnostics() { # diagnostics <out-file>
  worker_get /api/chat/diagnostics "$1"
}

printf 'label\tprompt_tokens\tcompletion_tokens\tengine_elapsed_s\twall_s\ttok_per_s\tpeak_mlx_bytes\tactive_mlx_bytes\tload_s\tswap_before_mb\tswap_after_mb\tswap_peak_mb\tresult\tnote\n' >> "$RESULTS"

# The load probe: the worker lazy-loads, and its own `elapsed_seconds` covers only the generation,
# so load time is measured from outside — from the request to the worker reporting `loaded: true`.
load_probe() { # load_probe <out-file>
  # `-u` matters: the probe is killed when the request returns, and a buffered print would be lost.
  "$PY" -u - "$URL" "$TOKEN" >"$1" 2>/dev/null <<'PY' &
import json, sys, time, urllib.request
url, token = sys.argv[1], sys.argv[2]
t0 = time.time()
answer = "?"
while time.time() - t0 < 600:
    try:
        request = urllib.request.Request(url + "/api/chat/diagnostics",
                                         headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(request, timeout=5) as response:
            state = json.loads(response.read())
        if state.get("loaded"):
            answer = "%.2f" % (time.time() - t0)
            break
    except Exception:
        pass
    time.sleep(0.5)
print("load_seconds=" + answer)
PY
}

run_chat() { # run_chat <label> <prompt> <max_tokens> <watch_load 0|1>
  local label="$1" prompt="$2" tokens="$3" watch="$4"
  local log="$LOGS/$label.log" body="$LOGS/$label.request.json" out="$OUT/$label.json"
  local meta="$LOGS/$label.meta" facts="$LOGS/$label.facts"
  local sw_before co_before
  sw_before="$(swap_used_mb || true)"; co_before="$(compressor_pages || true)"
  "$PY" - "$prompt" "$tokens" "$body" <<'PY'
import json, sys
prompt, tokens, path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
json.dump({"messages": [{"role": "user", "content": prompt}], "max_tokens": tokens,
           "temperature": 0.0, "top_p": 1.0, "seed": 42, "thinking": False, "stream": False},
          open(path, "w"))
PY

  echo
  echo "=== $label (max_tokens $tokens) ==="
  local peak_file killer="" wallpid="" loadpid="" curlpid
  peak_file="$LOGS/$label.swap-peak"; : > "$peak_file"
  # Sample swap while it runs: on the small tier the peak is the whole question. The first sample
  # is taken immediately, because a short run can finish before a 3 s sleep ever fires — a peak
  # file that was never written is reported as "?" rather than as a 0 that looks like a reading.
  # No `local` in here: this is a subshell, not the function body. Both background jobs send their
  # output to /dev/null, which is load-bearing: a killed subshell leaves its `sleep` child, and a
  # child still holding this script's stdout keeps a pipe such as `| tail` open until it expires.
  ( while :; do
      now="$(swap_used_mb || true)"
      if [ -n "${now:-}" ]; then
        prev="$(cat "$peak_file" 2>/dev/null)"
        if [ -z "$prev" ] || awk -v a="$now" -v b="$prev" 'BEGIN{exit !(a>b)}'; then echo "$now" > "$peak_file"; fi
        if awk -v a="$now" -v l="$SWAP_LIMIT_GB" 'BEGIN{exit !(a > l*1024)}'; then
          echo "swap exceeded ${SWAP_LIMIT_GB} GB - stopping this run" >> "$log"
          stop_worker
          break
        fi
      fi
      sleep 3
    done ) >/dev/null 2>&1 & killer=$!

  local loadfile="$LOGS/$label.load"
  if [ "$watch" = 1 ]; then load_probe "$loadfile"; loadpid=$!; fi
  "$CURL" -sS --max-time "$WALL_LIMIT" -o "$out" -w '%{http_code} %{time_total}' \
    -X POST "$URL/v1/chat/completions" -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' --data-binary @"$body" >"$meta" 2>"$log" &
  curlpid=$!
  ( sleep "$WALL_LIMIT"; if kill -0 "$curlpid" 2>/dev/null; then
      echo "wall limit ${WALL_LIMIT}s reached - stopping" >> "$log"; stop_worker; fi ) >/dev/null 2>&1 & wallpid=$!
  wait "$curlpid"; local rc=$?
  kill "$killer" "$wallpid" 2>/dev/null; wait "$killer" "$wallpid" 2>/dev/null
  if [ -n "$loadpid" ]; then
    # One more poll after the request returns. `status()` reads `loaded` before it may block on
    # MLX's own lock, so a poll that started during the load can answer with a stale `false`; this
    # grace lets the next one see the truth instead of losing the only load measurement there is.
    sleep 1
    kill "$loadpid" 2>/dev/null; wait "$loadpid" 2>/dev/null
  fi
  local wall; wall="$(awk 'NF>=2 {printf "%.2f", $2; exit}' "$meta" 2>/dev/null)"
  [ -n "$wall" ] || wall="?"
  local sw_after; sw_after="$(swap_used_mb || true)"
  local http; http="$(awk '{print $1; exit}' "$meta" 2>/dev/null)"

  "$PY" - "$out" >"$facts" 2>/dev/null <<'PY'
import json, sys
def value(item):
    """An absent field is absent — "None" is not a measurement."""
    return "" if item is None else item
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("unreadable=1"); raise SystemExit
usage = d.get("usage") or {}
studio = d.get("studio_hub") or {}
content = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
detail = d.get("detail") if isinstance(d.get("detail"), str) else ""
print("prompt_tokens=%s" % value(usage.get("prompt_tokens")))
print("completion_tokens=%s" % value(usage.get("completion_tokens")))
print("engine_elapsed_s=%s" % value(studio.get("elapsed_seconds")))
print("answer=%s" % content.replace("\n", " ").replace("\t", " ").strip())
print("detail=%s" % detail.replace("\n", " ").replace("\t", " ").strip()[:200])
print("json_valid=%s" % value(studio.get("json_valid")))
PY
  local ptok ctok eload lsec tok_s note="" result
  ptok="$(fact "$facts" prompt_tokens)"; ctok="$(fact "$facts" completion_tokens)"
  eload="$(fact "$facts" engine_elapsed_s)"; lsec="$(fact "$loadfile" load_seconds)"
  tok_s="$(awk -v c="${ctok:-0}" -v e="${eload:-0}" 'BEGIN{ if (c+0>0 && e+0>0) printf "%.2f", c/e; else print "?" }')"
  case "$http" in ''|000) http="?";; esac

  if grep -q 'swap exceeded' "$log" 2>/dev/null; then
    result="ABORTED: swap limit"
  elif grep -q 'wall limit' "$log" 2>/dev/null; then
    result="ABORTED: wall limit"
  elif grep -qiE 'out of memory|OutOfMemory|MemoryError|bad_alloc|Impacting Interactivity|kIOGPUCommandBuffer|insufficient memory' "$log" "$WORKER_LOG" 2>/dev/null; then
    result="FAILED: out of memory / GPU abort"
    note="$(grep -iE 'out of memory|OutOfMemory|MemoryError|bad_alloc|Impacting Interactivity|kIOGPUCommandBuffer|insufficient memory' "$log" "$WORKER_LOG" 2>/dev/null | tail -1)"
  elif [ "$rc" -ne 0 ]; then
    result="FAILED: request ended without a reply (curl exit $rc)"
  elif [ "$http" = "200" ] && [ -n "$(fact "$facts" completion_tokens)" ]; then
    result="OK"
  elif [ "$http" = "409" ]; then
    result="REFUSED by the worker: $(fact "$facts" detail)"
  elif [ "$http" = "?" ]; then
    result="FAILED: no HTTP status"
  else
    result="FAILED: HTTP $http $(fact "$facts" detail)"
  fi
  note="$(printf '%s' "$note" | tr '\t\r\n' '   ' | cut -c1-160)"

  # mlx's peak is a process high-water, read while the model is still resident. It is recorded
  # after every run so the table shows where the high-water came from, and "?" when the worker was
  # stopped before it could answer.
  local peak_bytes="" active_bytes=""
  diagnostics "$LOGS/$label.diagnostics.json"
  peak_bytes="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('peak_memory_bytes') or '')" \
    "$LOGS/$label.diagnostics.json" 2>/dev/null)"
  active_bytes="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('active_memory_bytes') or '')" \
    "$LOGS/$label.diagnostics.json" 2>/dev/null)"

  {
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$label" "${ptok:-?}" "${ctok:-?}" "${eload:-?}" "$wall" "$tok_s" \
      "${peak_bytes:-?}" "${active_bytes:-?}" "${lsec:-?}" \
      "${sw_before:-?}" "${sw_after:-?}" "$(peak_swap "$peak_file")" "$result" "$note"
  } >> "$RESULTS"
  echo "    $result   wall ${wall}s   engine ${eload:-?}s   ${tok_s} tok/s   swap peak $(peak_swap "$peak_file") MB   peak MLX ${peak_bytes:-?} B"
}

# ── the runs ───────────────────────────────────────────────────────────────────────
if [ "$SMOKE" = 1 ]; then
  # Proves the plumbing — app discovery, catalog lookup, model discovery, worker start, the HTTP
  # contract, the swap sampler, the TSV, the diagnostics parse — without spending an hour of a
  # small Mac's time. The output folder was redirected to a temporary directory above, so a smoke
  # check never disturbs a real run and prints no verdict.
  echo "SMOKE MODE: load the real model, one 8-token completion, into a temporary folder."
  run_chat "smoke" "Reply with the single word OK." 8 1
  diagnostics "$OUT/smoke-diagnostics.json"
  echo
  echo "Harness works if the row above says OK. Temporary output: $OUT"
  echo "This was a harness check, not a measurement — nothing is reported from it."
  stop_worker
  exit 0
fi

# 00 loads the model and measures the load; 01 is the measured run; 02 repeats it warm, so the
# two together show whether the first number was a fluke.
run_chat "00-load" "Say hello." 1 1
run_chat "01-story" "$PROMPT" "$MAX_TOKENS" 0
run_chat "02-story-repeat" "$PROMPT" "$MAX_TOKENS" 0

# Memory and residency, read while the model is still resident: mlx's peak is a process
# high-water, so it must be read before the release below.
diagnostics "$OUT/diagnostics.json"
PEAK_BYTES="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('peak_memory_bytes') or '')" "$OUT/diagnostics.json" 2>/dev/null)"
ACTIVE_BYTES="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('active_memory_bytes') or '')" "$OUT/diagnostics.json" 2>/dev/null)"
MLX_VERSION="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('mlx') or '')" "$OUT/diagnostics.json" 2>/dev/null)"
"$CURL" -sS --max-time 60 -X POST "$URL/api/memory/release" -H "Authorization: Bearer $TOKEN" \
  -o "$LOGS/release.json" 2>/dev/null
RELEASED="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('resident'))" "$LOGS/release.json" 2>/dev/null)"
stop_worker
PEAK_GB="$(awk -v b="${PEAK_BYTES:-}" 'BEGIN{if (b+0>0) printf "%.2f", b/1e9; else print "?"}')"
ACTIVE_GB="$(awk -v b="${ACTIVE_BYTES:-}" 'BEGIN{if (b+0>0) printf "%.2f", b/1e9; else print "?"}')"

# ── the record ─────────────────────────────────────────────────────────────────────
echo
echo "────────────────────────────────────────────────────────────────────────"
if [ -n "$COLUMN" ]; then "$COLUMN" -t -s "$(printf '\t')" "$RESULTS"; else cat "$RESULTS"; fi
echo "────────────────────────────────────────────────────────────────────────"

tsv() { awk -F'\t' -v l="$1" -v c="$2" '$1==l {print $c; exit}' "$RESULTS"; }
is_ok() { [ "$1" = "OK" ]; }
r00="$(tsv 00-load 13)"; r01="$(tsv 01-story 13)"; r02="$(tsv 02-story-repeat 13)"
tok_s_01="$(tsv 01-story 6)"; tok_s_02="$(tsv 02-story-repeat 6)"
answer="$(fact "$LOGS/01-story.facts" answer)"

if is_ok "$r00" && is_ok "$r01" && is_ok "$r02"; then
  if [ "$ON_TARGET" != yes ]; then
    verdict="NOT TIER EVIDENCE — the runs completed at ${tok_s_01}–${tok_s_02} tok/s on this ${MEM_GB} GB Mac, which is not an 8 GB or 16 GB tier. The numbers are real; they answer no tier question and must not be compared with one."
  elif [ "$TIER" = "8 GB" ]; then
    verdict="COMPLETED BELOW THE PUBLISHED TIER — Qwen3.5-9B 4-bit finished a ${MAX_TOKENS}-token completion on this 8 GB Mac at ${tok_s_01}–${tok_s_02} tok/s, peak MLX ${PEAK_GB} GB. It ran; that is not the same as comfortable, and the manifest still qualifies the model at ${QUAL_GB:-16} GB."
  else
    verdict="QUALIFIED — Qwen3.5-9B 4-bit completed a ${MAX_TOKENS}-token completion on this 16 GB Mac at ${tok_s_01}–${tok_s_02} tok/s, peak MLX ${PEAK_GB} GB, load $(tsv 00-load 9) s. This is the answer the 16 GB tier was missing."
  fi
elif [ "${r01#ABORTED}" != "$r01" ] || [ "${r00#ABORTED}" != "$r00" ] || [ "${r02#ABORTED}" != "$r02" ]; then
  verdict="INCONCLUSIVE — a run was stopped by the harness (00 \"$r00\", 01 \"$r01\", 02 \"$r02\"), so the tier is neither proven nor disproven. Re-run with CHAT_MAX_SWAP_GB=10 (or a longer CHAT_TIMEOUT) to finish it, or accept that this tier needs the swap it was not allowed."
elif [ "${r01#REFUSED}" != "$r01" ] || [ "${r00#REFUSED}" != "$r00" ]; then
  verdict="REFUSED BY THE WORKER — 00 \"$r00\", 01 \"$r01\". That is the product's own gate answering; it is not a throughput result."
else
  verdict="NOT QUALIFIED — 00 \"$r00\", 01 \"$r01\", 02 \"$r02\" (peak MLX ${PEAK_GB} GB, load $(tsv 00-load 9) s, swap peak $(tsv 01-story 12) MB). A run fails on this tier when memory runs out, the GPU aborts, or the model cannot load."
fi
case "$TIER" in
  "8 GB") other_line="The 16 GB tier was NOT run here: this block says nothing about it." ;;
  "16 GB") other_line="The 8 GB tier was NOT run here: this block says nothing about it." ;;
  *) other_line="No 8 GB or 16 GB tier was run here: this block says nothing about either." ;;
esac
if [ "$SMOKE" = 0 ] && [ "$TIER" = "8 GB" ]; then
  other_line="$other_line The app's own rule publishes ${QUAL_GB:-16} GB as the qualified tier; whether it refuses or warns on 8 GB is in the worker catalog line above."
fi

{
  echo "==================== COPY FROM HERE ===================="
  echo "${TIER_LABEL} · Qwen3.5-9B 4-bit (mlx-community/Qwen3.5-9B-4bit, uniform 4-bit MLX) — ${MAX_TOKENS}-token completion"
  echo "Machine: $CHIP · $MEM_GB GB unified memory · macOS $OSV · ${MODEL_ID:-unknown}"
  [ "$ON_TARGET" = yes ] || echo "WARNING: this Mac is not an 8/16 GB tier, so these numbers are real but are NOT tier evidence."
  echo "App: Studio Hub $APP_VERSION ($APP_COMMIT) · runtime $PY"
  echo "Model: $MODEL_ID (uniform 4-bit MLX) @ $REV · ${MODEL_ON_DISK:-?} GB on disk, ${MODEL_FILES:-?} file(s) · $MODEL_DIR"
  echo "Manifest qualifies it at: ${QUAL_GB:-none published} GB (qualification.minimum_unified_memory_gb)"
  echo "Engine: mlx ${MLX_VERSION:-?} / mlx-lm (bundled) · catalog context ${CAT_CONTEXT:-?} / max output ${CAT_MAXOUT:-?} tokens"
  echo "Worker's own host verdict: eligible=$CAT_ELIGIBLE, ready=$CAT_AVAILABLE${MEMORY_WARNING:+, warning: $MEMORY_WARNING}"
  # This sentence used to read "the shipped default is immediate, which would release the model
  # between runs". That is wrong on the tier this script exists to measure: the shipped default is
  # `auto`, and `auto` resolves to `balanced` at 16 GiB and above (components/memory_modes.py,
  # AUTO_BALANCED_MIN_BYTES), which HOLDS the model for ten idle minutes. A verdict block that
  # misstated the shipped default is the one line here an operator would act on.
  echo "Memory policy pinned to: $POLICY_MODE for the measurement (the shipped default is auto, which on a 16 GB Mac resolves to balanced and holds the model for 10 idle minutes; this run pins it so no measurement can be affected by a release between runs)"
  echo "Prompt (${MAX_TOKENS} max tokens, temperature 0, top_p 1, seed 42, thinking off): $PROMPT"
  echo "Memory at start: ${AVAIL_GB:-?} GB available (floor ${FLOOR_GB:-?} GB), swap ${SWAP_START_MB:-?} MB used"
  # A run that began below the floor is not the same evidence as one that did not: it ran
  # against less room than the checkpoint asks for, which is the point on 8 GB and a caveat
  # everywhere. The block says which it was rather than leaving the reader to infer it.
  [ "${STARTED_BELOW_FLOOR:-no}" = yes ] && echo "STARTED BELOW THE FLOOR: this run had less memory available than the weights plus working room; the swap cap and the timeout are what bounded it."
  printf '  %-17s %8s %8s %10s %8s %9s %11s %10s  %s\n' run prompt_tok compl_tok engine_s wall_s tok_per_s swap_peak load_s result
  for label in 00-load 01-story 02-story-repeat; do
    printf '  %-17s %8s %8s %10s %8s %9s %11s %10s  %s\n' \
      "$label" "$(tsv "$label" 2)" "$(tsv "$label" 3)" "$(tsv "$label" 4)" "$(tsv "$label" 5)" \
      "$(tsv "$label" 6)" "$(tsv "$label" 12)" "$(tsv "$label" 9)" "$(tsv "$label" 13)"
  done
  echo "Legend: engine_s is the worker's own generation time; wall_s is the whole HTTP request; tok/s = completion tokens / engine_s; load_s is measured on 00 only (the first request, which loads the model)."
  echo "Peak MLX memory (worker high-water after all runs): ${PEAK_GB} GB (${PEAK_BYTES:-?} bytes); active at peak: ${ACTIVE_GB} GB; model released after the runs: resident=$RELEASED"
  echo "Answer (01-story, ${MAX_TOKENS} max tokens): $(printf '%s' "${answer:-<none>}" | cut -c1-300)"
  echo "Verdict: $verdict"
  echo "$other_line"
  echo "===================== TO HERE ============================"
} | tee "$OUT/VERDICT.txt"

echo
echo "Results: $RESULTS"
echo "Logs:    $LOGS"
echo "Paste back the block above — it is also saved as $OUT/VERDICT.txt"
