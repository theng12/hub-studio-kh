#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
OUTPUT_ARGUMENT="${1:-$PROJECT_ROOT/.build/music-runtime}"
ENGINE_SOURCE="${2:-$PROJECT_ROOT/.build/music-engine-source}"
BASE_PYTHON="${3:-$PROJECT_ROOT/.build/voice-runtime/bin/python3.12}"

case "$OUTPUT_ARGUMENT" in
  /*) OUTPUT="$OUTPUT_ARGUMENT" ;;
  *) OUTPUT="$PROJECT_ROOT/$OUTPUT_ARGUMENT" ;;
esac
case "$OUTPUT" in
  "$PROJECT_ROOT"/*) ;;
  *) echo "Output must stay inside $PROJECT_ROOT" >&2; exit 1 ;;
esac
[[ ! -e "$OUTPUT" ]] || { echo "Refusing to overwrite existing output: $OUTPUT" >&2; exit 1; }
[[ -x "$BASE_PYTHON" ]] || { echo "Pinned Voice runtime is unavailable: $BASE_PYTHON" >&2; exit 1; }
[[ -d "$ENGINE_SOURCE/acestep" ]] || { echo "Pinned ACE-Step source is unavailable: $ENGINE_SOURCE" >&2; exit 1; }
command -v uv >/dev/null || { echo "Missing build command: uv" >&2; exit 1; }

mkdir -p "$(dirname "$OUTPUT")"
STAGE="$(mktemp -d "$(dirname "$OUTPUT")/music-runtime-stage.XXXXXX")"
cleanup() { [[ ! -d "$STAGE" ]] || rm -rf "$STAGE"; }
trap cleanup EXIT

uv pip install \
  --python "$BASE_PYTHON" \
  --target "$STAGE" \
  --no-deps \
  --require-hashes \
  -r "$SCRIPT_DIR/requirements.lock.txt"
mkdir -p "$STAGE/manifest"
cp "$SCRIPT_DIR/requirements.lock.txt" "$STAGE/manifest/requirements.lock.txt"
cp "$SCRIPT_DIR/overlay-manifest.json" "$STAGE/manifest/music-runtime.json"

"$BASE_PYTHON" -I -B - "$STAGE" "$ENGINE_SOURCE" <<'PY'
import importlib.metadata
import json
from pathlib import Path
import sys

overlay, engine = map(Path, sys.argv[1:])
sys.path[:0] = [str(engine), str(overlay)]
expected = json.loads((overlay / "manifest/music-runtime.json").read_text())["packages"]
for distribution, version in expected.items():
    assert importlib.metadata.version(distribution) == version, distribution
from acestep.handler import AceStepHandler
from acestep.llm_inference import LLMHandler
from acestep.inference import GenerationConfig, GenerationParams, generate_music
assert AceStepHandler and LLMHandler and GenerationConfig and GenerationParams and generate_music
PY

find "$STAGE" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
find "$STAGE" -depth -type d -name __pycache__ -empty -delete
chmod -R a-w "$STAGE"
mv "$STAGE" "$OUTPUT"
trap - EXIT
echo "Music runtime overlay ready: $OUTPUT"
