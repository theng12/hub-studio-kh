#!/bin/sh
# Double-click this to measure ONE video generation through the Video worker's own code path.
#
# Why a .command: Finder has no application bound to a `.sh`, so double-clicking
# `ltx-video-worker-run.py` opens it in an editor and looks broken.
#
# What it answers, and how it differs from RUN-16GB-LTX-VIDEO.command beside it:
#
#   RUN-16GB-LTX-VIDEO.command  drives the standalone qualification runner. It answers "can this
#                               model produce a video on this Mac at all, and what does it cost?"
#                               It is the exploratory measurement, and it bypasses the lane's
#                               memory tier on purpose.
#   RUN-LTX-VIDEO-WORKER.command (this) drives the *shipped worker*: the same FastAPI app the Hub
#                               talks to, through the worker's own routes, with the worker's own
#                               memory policy and artifact checks. It answers "does the lane we
#                               ship actually run on this Mac, and what does the lane cost?" —
#                               including the honest refusal when the Mac is below the tier the
#                               manifest publishes.
#
# Both write a results folder beside themselves and print the same headline numbers: wall-clock
# seconds, peak MLX Metal allocation, peak process footprint (the statistic that counts), and the
# MP4's real duration and track counts.
#
# It changes nothing on this Mac: it installs no app, moves no model, and deletes nothing.
# It writes only inside its own results folder, plus the package receipt the app writes when a
# model is verified for the first time.
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
RUNNER="$HERE/ltx-video-worker-run.py"
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$HERE/ltx-video-worker-results-$STAMP"

echo "Studio Hub — LTX-2.3 video measurement through the Video worker"
echo "=============================================================="
echo

if [ ! -f "$RUNNER" ]; then
    echo "ltx-video-worker-run.py is not beside this file."
    echo "Keep RUN-LTX-VIDEO-WORKER.command and ltx-video-worker-run.py in the same folder."
    echo "Looked in: $HERE"
    echo
    read -r _ || true
    exit 1
fi

# ---- the machine, read with absolute sysctl paths (a bare `sysctl` is not on PATH for a
# ---- packaged worker, and that omission once made every Voice memory verdict "unknown")
SYSCTL=/usr/sbin/sysctl
[ -x "$SYSCTL" ] || SYSCTL=/usr/bin/sysctl
CHIP=$("$SYSCTL" -n machdep.cpu.brand_string 2>/dev/null || echo unknown)
MODEL=$("$SYSCTL" -n hw.model 2>/dev/null || echo unknown)
MEMBYTES=$("$SYSCTL" -n hw.memsize 2>/dev/null || echo 0)
MEMGB=$(( MEMBYTES / 1073741824 ))
echo "This Mac: $CHIP ($MODEL), ${MEMGB} GB unified memory, macOS $(sw_vers -productVersion 2>/dev/null || echo unknown)"
echo "swap now: $("$SYSCTL" -n vm.swapusage 2>/dev/null || echo unknown)"
echo

# ---- the runtime: the app's own video runtime, which is what the packaged worker runs on
RUNTIME=""
RUNTIME_REJECTED=""
for cand in \
    "${STUDIOHUB_VIDEO_RUNTIME:-}" \
    "/Applications/Hub Studio KH.app/Contents/Resources/runtimes/video/bin/python3" \
    "$HERE/video-runtime/bin/python3" \
    "$HERE/../.build/video-runtime/bin/python3"
do
    [ -n "$cand" ] || continue
    [ -x "$cand" ] || continue
    if "$cand" -c 'import mlx.core, fastapi, multipart' 2>/dev/null; then
        RUNTIME="$cand"
        break
    fi
    [ -n "$RUNTIME_REJECTED" ] || RUNTIME_REJECTED="$cand exists but cannot import mlx + fastapi"
done
if [ -z "$RUNTIME" ]; then
    echo "The Video worker's runtime is not on this Mac, so there is nothing to measure with."
    [ -n "$RUNTIME_REJECTED" ] && echo "  ($RUNTIME_REJECTED)"
    echo
    echo "Install Studio Hub first — this test drives the app's own components and runtime:"
    echo "  /Applications/Hub Studio KH.app/Contents/Resources/runtimes/video/bin/python3"
    echo
    echo "Then double-click this again."
    echo
    read -r _ || true
    exit 1
fi
echo "runtime:  $RUNTIME"
echo "          $("$RUNTIME" -c 'import mlx.core as mx, sys; print(sys.version.split()[0], "mlx", mx.__version__)' 2>/dev/null || echo 'mlx NOT importable')"

# ---- where components/ and model-manifests/ live: the app bundle on a node, a checkout here
HUB_ROOT="${STUDIOHUB_HUB_ROOT:-}"
if [ -z "$HUB_ROOT" ]; then
    for cand in \
        "/Applications/Hub Studio KH.app/Contents/Resources" \
        "$HERE/../.build/app/Contents/Resources" \
        "$HERE/.."
    do
        [ -f "$cand/components/video/app/backend/main.py" ] && { HUB_ROOT="$cand"; break; }
    done
fi
if [ -z "$HUB_ROOT" ]; then
    echo
    echo "Studio Hub's components are not on this Mac. Install the app, or set"
    echo "STUDIOHUB_HUB_ROOT to a checkout or to an app's Contents/Resources folder."
    echo
    read -r _ || true
    exit 1
fi
echo "hub:      $HUB_ROOT"

# ---- the model, and the tier the lane publishes for it
#
# The tier is printed *before* the run, because on a 16 GB Mac the correct result of this test is
# a refusal, and a refusal that arrives without explanation reads as a broken test.
MODELS="${STUDIOHUB_MODELS_DIR:-$HOME/StudioHubModels}"
MANIFEST="$HUB_ROOT/model-manifests/ltx-2-3-22b-ram-12gb-mlx.json"
TIER=""
RECOMMENDED=""
if [ -f "$MANIFEST" ]; then
    TIER=$("$RUNTIME" -c "import json,sys; print(json.load(open(sys.argv[1]))['qualification'].get('minimum_unified_memory_gb') or '')" "$MANIFEST" 2>/dev/null)
    RECOMMENDED=$("$RUNTIME" -c "import json,sys; print(json.load(open(sys.argv[1]))['qualification'].get('recommended_unified_memory_gb') or '')" "$MANIFEST" 2>/dev/null)
fi
echo "model:    ltx-2-3-22b-ram-12gb-mlx (LTX-2.3 22B RAM-12GB, mixed 2-8 bit) in $MODELS"
echo "tier:     ${TIER:-unknown} GB published minimum${RECOMMENDED:+, ${RECOMMENDED} GB recommended}"
echo
if [ -n "$TIER" ] && [ "$MEMGB" -lt "$TIER" ]; then
    echo "NOTE — this Mac has ${MEMGB} GB and the lane publishes a ${TIER} GB floor for this model."
    echo "       The expected result here is a REFUSAL, and that refusal is this test passing,"
    echo "       not failing: it is the lane declining to start a job it would have to swap"
    echo "       through. Whether the model could run here anyway is a different question, and"
    echo "       RUN-16GB-LTX-VIDEO.command in this folder is the test that asks it."
    echo
fi

# ---- a source scene, if one is supplied: image-to-video is the heavier of the two doors
IMG="${STUDIOHUB_LTX_IMAGE:-}"
if [ -n "$IMG" ]; then
    echo "door:     image-to-video ($IMG)"
else
    echo "door:     text-to-video (set STUDIOHUB_LTX_IMAGE=/path/to/scene.png for image-to-video)"
fi
echo

echo "starting. this can take a long time; the log is kept either way."
echo
mkdir -p "$OUT"
STATUS=0
"$RUNTIME" "$RUNNER" \
    --models-dir "$MODELS" \
    --results "$OUT" \
    --verify \
    ${STUDIOHUB_PROMPT:+--prompt "$STUDIOHUB_PROMPT"} \
    ${IMG:+--image "$IMG"} \
    ${STUDIOHUB_ASPECT:+--aspect-ratio "$STUDIOHUB_ASPECT"} \
    ${STUDIOHUB_RESOLUTION:+--resolution "$STUDIOHUB_RESOLUTION"} \
    ${STUDIOHUB_DURATION:+--duration "$STUDIOHUB_DURATION"} \
    2>&1 | tee "$OUT/run.log" || STATUS=$?

echo
echo "----- what the lane recorded -----"
if [ -f "$OUT/measurement.json" ]; then
    "$RUNTIME" - "$OUT/measurement.json" <<'PY' || true
import json, sys
record = json.load(open(sys.argv[1]))
result = record.get('result', {})
job = record.get('job') or {}
print(f"status:            {result.get('status')}")
if result.get('reason'):
    print(f"reason:            {result['reason']}")
if job:
    print(f"job:               {job.get('id')} ({job.get('state')})")
    print(f"wall seconds:      {job.get('duration_seconds')}")
    print(f"peak MLX bytes:    {job.get('peak_mlx_bytes')}")
    print(f"peak footprint:    {job.get('peak_footprint_bytes')}")
    print(f"resolution:        {job.get('width')}x{job.get('height')} x {job.get('frames')} frames")
artifact = record.get('artifact') or {}
boxes = artifact.get('boxes') or {}
if boxes:
    print(f"artifact:          {artifact.get('path')}")
    print(f"  duration:        {boxes.get('duration_seconds')} s")
    print(f"  tracks:          {boxes.get('video_tracks')} video, {boxes.get('audio_tracks')} audio")
PY
else
    echo "no measurement.json was written — the run did not get as far as recording anything"
fi
echo
echo "Results folder: $OUT"
echo "Send that folder back (or just its measurement.json) and nothing else is needed."
if [ "$STATUS" -ne 0 ]; then
    echo "The run did not finish cleanly (exit $STATUS); the reason is in the log above."
fi
echo "Nothing was installed, moved or deleted."
echo
read -r _ || true
exit "$STATUS"
