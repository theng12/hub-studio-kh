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
#
# **A candidate counts only if it can import the pack's own modules.** The list used to be
# "first path that exists", so a Mac whose runtime was the app's (`runtimes/video`, which the
# packaged worker itself runs on) was told the runtime "is not on this Mac" while one sat inside
# the bundle — and a half-built environment that exists but cannot import `ltx_pipelines_mlx`
# was chosen over a working one later in the list.
RUNTIME=""
RUNTIME_REJECTED=""
for cand in \
    "${STUDIOHUB_LTX_RUNTIME:-}" \
    "$HERE/ltx-runtime/env/bin/python" \
    "$HERE/video-runtime/bin/python3" \
    "/Applications/Studio Hub.app/Contents/Resources/runtimes/video/bin/python3" \
    "$HERE/../.build/ltx-2-mlx/env/bin/python" \
    "$HERE/../.build/video-runtime/bin/python3" \
    "$HOME/StudioHubModels/ltx-runtime/env/bin/python"
do
    [ -n "$cand" ] || continue
    [ -x "$cand" ] || continue
    if "$cand" -c 'import mlx.core, ltx_pipelines_mlx' 2>/dev/null; then
        RUNTIME="$cand"
        break
    fi
    [ -n "$RUNTIME_REJECTED" ] || RUNTIME_REJECTED="$cand exists but cannot import mlx + ltx_pipelines_mlx"
done
if [ -z "$RUNTIME" ]; then
    echo "The LTX runtime is not on this Mac, so there is nothing to measure with."
    [ -n "$RUNTIME_REJECTED" ] && echo "  ($RUNTIME_REJECTED)"
    echo
    echo "The Studio Hub app ships it: /Applications/Studio Hub.app/Contents/Resources/runtimes/video"
    echo "is the same Python 3.11 environment the packaged video worker runs on, so a Mac with the"
    echo "app installed already has everything this script needs. Otherwise it is a separate"
    echo "Python 3.11 environment (the Image runtime is 3.12 and cannot host it)."
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
#
# **The drive comes before `~/StudioHubModels`, and that order is the fix for a wasted run.**
# It used to be the other way round, so this script picked a **10 GB partial** copy on the root
# volume over the complete **17.81 GiB** one on NOVA — the two differ because an old rsync was
# interrupted — and the failure that produced was
#   `[load_safetensors] … invalid data offsets … Perhaps an incomplete download or corrupt file?`
# which reads as "the model is corrupt" when the truth is "this copy is unfinished". A complete
# copy on a mounted drive is worth more than a nearer partial one.
MODEL_DIR=""
for cand in \
    "${STUDIOHUB_LTX_MODEL:-}" \
    "$HERE/ltx-2.3-22b-ram-12gb-mlx" \
    /Volumes/KH-STUDIO-*/model-library/video-models/ltx-2.3-22b-ram-12gb-mlx \
    "$HOME/StudioHubModels/ltx-2.3-22b-ram-12gb-mlx"
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

# A partial copy fails late and confusingly, so say so here instead. `du -sk` of the complete
# package on NOVA is 18,674,324 KiB; anything under 90% of that is a copy that did not finish.
MODEL_KB=$(du -sk "$MODEL_DIR" 2>/dev/null | cut -f1)
COMPLETE_KB=18674324
if [ -n "$MODEL_KB" ] && [ "$MODEL_KB" -lt $((COMPLETE_KB * 90 / 100)) ]; then
    echo
    echo "WARNING: this copy is $SIZE against the package's 17.81 GiB, so it looks unfinished."
    echo "         A partial copy does not fail as a missing file — it fails deep inside"
    echo "         load_safetensors as a shape error, which reads as a corrupt model."
    echo "         The complete copy is on the KH-STUDIO drives at model-library/video-models/."
    echo
fi

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
#
# **Block streaming is opt-in (`LTX_LOW_RAM_STREAMING=1`), and it does not work with this pack.**
# It was passed unconditionally — the research had called it "the cheapest route to a real 16 GB
# lane", so it was worth trying — and on the baa-ai pack it fails before generating anything:
#   `ValueError: Received 2 parameters not in model: video_to_audio_attn.to_gate_logits.{biases,scales}`
# The streaming block does not carry those two keys, and the pack never enables streaming itself,
# so the pack's own configuration is the one that runs. That hope is now measured rather than
# assumed; leave the flag off unless a pack is known to take it.
echo "starting. this can take a long time on a 16 GB Mac; the log is kept either way."
if [ -n "${LTX_LOW_RAM_STREAMING:-}" ]; then
    echo "streaming: on (LTX_LOW_RAM_STREAMING) — this pack is known to fail with it"
else
    echo "streaming: off — the pack's own configuration is the one that runs"
fi
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
    ${LTX_LOW_RAM_STREAMING:+--low-ram-streaming} \
    2>&1 | tee "$OUT/run.log"

echo
echo "----- peak process footprint (from /usr/bin/time -l) -----"
grep -E "maximum resident set size" "$OUT/run.log" || echo "(not reported)"
echo
echo "Results folder: $OUT"
echo "Nothing was installed, moved or deleted."
