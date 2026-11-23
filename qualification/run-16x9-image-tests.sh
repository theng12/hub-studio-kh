#!/bin/bash
# 16:9 text-rendering tests — 1280x720 — Z-Image-Turbo vs FLUX.2 Klein.
#
#   bash qualification/run-16x9-image-tests.sh golden|dense
#
# Owner's spec (2026-09-24): landscape 16:9, 1280x720. Two images — one with a 4-5 word golden
# magical title, one with titles plus a long body of text. Both models, with speeds.
#
# The two models do not run the same schedule and should not: FLUX.2 Klein is distilled for 4
# steps at guidance 1.0, which is exactly what the app submits today (R07 receipt), and Z-Image
# Turbo is a 9-step model whose guidance is ignored. Using one number for both would misreport
# one of them, so each is run the way its own engine expects.
#
# FLUX runs on the runtime the app actually ships (mflux 0.17.5) so its speed is the speed you
# get today. Z-Image runs on mflux 0.20.0, the version it was qualified on. The versions are
# recorded per run.
set -u
ROOT="$HOME/Developer/_orchestra/developer-media/hub-studio-kh/app"
export PATH="$HOME/.local/bin:$PATH"
OUT="$HOME/Desktop/StudioHub Image Text Review"
MEASURE="$ROOT/.build/text-tests/measure-image-memory.sh"   # the shared memory recorder
mkdir -p "$OUT"

ZPACK="$(ls -d "$HOME"/.cache/huggingface/hub/models--filipstrand--Z-Image-Turbo-mflux-4bit/snapshots/*/ 2>/dev/null | head -1)"
FLUXPACK="$(ls -d "$HOME"/StudioHubModels/flux2-klein-4b-mlx-4bit/*/model 2>/dev/null | head -1)"
FLUXBIN="$ROOT/.build/st03-image-runtime-worker/bin/mflux-generate-flux2"

[ -n "$ZPACK" ]   || { echo "Z-Image pack not found in the HF cache" >&2; exit 1; }
[ -n "$FLUXPACK" ] || { echo "FLUX.2 Klein pack not found under ~/StudioHubModels" >&2; exit 1; }
[ -x "$FLUXBIN" ] || { echo "shipped image runtime missing: $FLUXBIN" >&2; exit 1; }

# ── the two scenes ────────────────────────────────────────────────────────────────
# "golden": a 4-5 word golden magical title. The previous sleep-story scene used a two-word
# title ("GOLDEN SLUMBER"), which is what this replaces.
GOLDEN_TITLE='THE GOLDEN LIGHT OF DREAMS'
GOLDEN_PROMPT="YouTube sleep-story cover art, golden magical theme. Large elegant gold serif title text reading exactly \"$GOLDEN_TITLE\" across the top. A moonlit enchanted forest with a winding path, glowing golden fireflies and drifting sparkles, deep midnight-blue and gold palette, soft dreamy haze, cinematic, highly detailed."

# "dense": titles plus a long body of small text, for the density question.
DENSE_PROMPT_FILE="$ROOT/.build/text-tests/dense-prompt.txt"

case "${1:-}" in
  golden) SCENE=golden; PROMPT="$GOLDEN_PROMPT" ;;
  dense)
    SCENE=dense
    [ -f "$DENSE_PROMPT_FILE" ] || { echo "no dense prompt at $DENSE_PROMPT_FILE" >&2; exit 1; }
    PROMPT="$(cat "$DENSE_PROMPT_FILE")"
    ;;
  *) echo "usage: $0 golden|dense" >&2; exit 2 ;;
esac

echo "scene: $SCENE   model prompts are 1280x720 (16:9)"
echo

# ── Z-Image-Turbo, mflux 0.20.0, 9 steps ──────────────────────────────────────────
echo "=== Z-Image-Turbo ==="
bash "$MEASURE" "z-image 16:9 $SCENE" \
  mflux-generate-z-image-turbo \
    --model "$ZPACK" \
    --prompt "$PROMPT" \
    --width 1280 --height 720 --steps 9 --seed 42 \
    --output "$OUT/10 z-image-turbo 16x9 $SCENE.png"

# ── FLUX.2 Klein, the shipped runtime (mflux 0.17.5), 4 steps ─────────────────────
echo
echo "=== FLUX.2 Klein (the engine the app ships today) ==="
bash "$MEASURE" "flux2-klein 16:9 $SCENE" \
  "$FLUXBIN" \
    --model "$FLUXPACK" \
    --prompt "$PROMPT" \
    --width 1280 --height 720 --steps 4 --guidance 1.0 --seed 42 \
    --output "$OUT/11 flux2-klein 16x9 $SCENE.png"

echo
echo "Results appended to $ROOT/.build/text-tests/memory-results.tsv"
echo "Images in $OUT"
