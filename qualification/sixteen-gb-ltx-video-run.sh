#!/bin/sh
# Measure LTX-2.3 video generation on THIS Mac — the 16 GB tier first, because that is
# where the fleet is (15 of ~53 machines; 50 of ~53 are 16 GB or below).
#
# Wrapped by RUN-16GB-LTX-VIDEO.command so the owner can double-click it. Keep the two together.
#
# The published figure for this family is the packager's "at least 24 GB unified RAM for safe
# T2V inference" — a vendor claim, measured on an M4 Max with 64 GB. Nobody has run it on a
# 16 GB Mac. That is the whole point of this script.
#
# It changes nothing on this Mac: it installs no app, moves no model, and deletes nothing.
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$HERE/ltx-video-results-$STAMP"
mkdir -p "$OUT"

echo "Studio Hub — LTX-2.3 video measurement"
echo "======================================"
echo
echo "This measures ONE video generation on this Mac and writes the numbers to:"
echo "  $OUT"
echo
echo "It installs nothing, moves nothing, and deletes nothing."
echo

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

# ---- the runtime
RUNTIME=""
for cand in \
    "${STUDIOHUB_LTX_RUNTIME:-}" \
    "$HERE/ltx-runtime/env/bin/python" \
    "$HERE/../.build/ltx-2-mlx/env/bin/python" \
    "$HOME/StudioHubModels/ltx-runtime/env/bin/python"
do
    [ -n "$cand" ] && [ -x "$cand" ] && { RUNTIME="$cand"; break; }
done
if [ -z "$RUNTIME" ]; then
    echo "The LTX runtime is not on this Mac, so there is nothing to measure with."
    echo
    echo "It is a separate Python 3.11 environment (the Image runtime is 3.12 and cannot host it)."
    echo "Build it once with:"
    echo "  git clone https://github.com/dgrauet/ltx-2-mlx && cd ltx-2-mlx"
    echo "  git checkout 3b2e77fd73af5f08961f4436ae979c0579d5deed"
    echo "  uv venv env --python 3.11"
    echo "  uv pip install --python env/bin/python hatchling editables hatch-vcs"
    echo "  uv pip install --python env/bin/python 'setuptools<80' wheel"
    echo "  uv pip install --python env/bin/python -e . --no-build-isolation"
    echo
    echo "Then point this script at it:  STUDIOHUB_LTX_RUNTIME=/path/to/env/bin/python $0"
    echo "(the exact recipe, and why each step is needed, is in research/video-fleet-shortlist-2026-09-24.md)"
    exit 1
fi
echo "runtime:  $RUNTIME"
echo "          $("$RUNTIME" -c 'import mlx.core as mx; print("mlx", mx.__version__)' 2>/dev/null || echo 'mlx NOT importable')"

# ---- the model
MODEL_DIR=""
for cand in \
    "${STUDIOHUB_LTX_MODEL:-}" \
    "$HERE/ltx-2.3-22b-ram-12gb-mlx" \
    "$HOME/StudioHubModels/ltx-2.3-22b-ram-12gb-mlx" \
    /Volumes/KH-STUDIO-*/model-library/video-models/ltx-2.3-22b-ram-12gb-mlx
do
    [ -n "$cand" ] && [ -d "$cand" ] && { MODEL_DIR="$cand"; break; }
done
if [ -z "$MODEL_DIR" ]; then
    echo
    echo "The model is not on this Mac. LTX-2.3 22B RAM-12GB (mixed 2-8 bit, 17.81 GiB) is"
    echo "downloaded to the drive shelf 'model-library/video-models/' and backed up to every SSD,"
    echo "and a copy belongs on this Mac's own disk before measuring."
    echo
    echo "Point this script at it:  STUDIOHUB_LTX_MODEL=/path/to/ltx-2.3-22b-ram-12gb-mlx $0"
    exit 1
fi
SIZE=$(du -sh "$MODEL_DIR" 2>/dev/null | cut -f1)
echo "model:    $MODEL_DIR ($SIZE)"

# ---- a source scene for image-to-video (a requirement: the owner works scene-first)
IMG=""
for cand in "${STUDIOHUB_LTX_IMAGE:-}" "$HERE/scene.png" "$HERE/../scene.png"; do
    [ -n "$cand" ] && [ -f "$cand" ] && { IMG="$cand"; break; }
done
if [ -n "$IMG" ]; then
    echo "scene:    $IMG  (image-to-video)"
else
    echo "scene:    none supplied — a synthetic test frame will be used"
    echo "          drop a real scene at $HERE/scene.png to measure the scene-first path properly"
fi
echo

# ---- run it, capturing the process footprint that MLX's own counter does NOT include
echo "starting. this can take a long time on a 16 GB Mac; the log is kept either way."
echo
/usr/bin/time -l "$RUNTIME" "$HERE/ltx-video-measure.py" \
    --model-dir "$MODEL_DIR" \
    --output "$OUT/generated.mp4" \
    --results "$OUT/measurement.json" \
    --require-image-to-video \
    ${IMG:+--image "$IMG"} \
    --num-frames "${LTX_FRAMES:-97}" \
    --width "${LTX_WIDTH:-704}" \
    --height "${LTX_HEIGHT:-480}" \
    --low-ram-streaming \
    2>&1 | tee "$OUT/run.log"

echo
echo "----- peak process footprint (from /usr/bin/time -l) -----"
grep -E "maximum resident set size" "$OUT/run.log" || echo "(not reported)"
echo
echo "Results folder: $OUT"
echo "Nothing was installed, moved or deleted."
