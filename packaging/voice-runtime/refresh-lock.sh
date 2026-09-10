#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
PYTHON="$PROJECT_ROOT/.build/voice-runtime-base/bin/python3"

if [[ ! -x "$PYTHON" ]]; then
  echo "Build the base runtime first; missing $PYTHON" >&2
  exit 1
fi

cd "$PROJECT_ROOT"
env \
  UV_CACHE_DIR="$PROJECT_ROOT/.build/uv-cache" \
  UV_CUSTOM_COMPILE_COMMAND="packaging/voice-runtime/refresh-lock.sh" \
  uv pip compile \
    packaging/voice-runtime/requirements-pilot.txt \
    --constraint packaging/voice-runtime/constraints-sonoma.txt \
    --python "$PYTHON" \
    --no-python-downloads \
    --generate-hashes \
    --quiet \
    --output-file packaging/voice-runtime/requirements.lock.txt
