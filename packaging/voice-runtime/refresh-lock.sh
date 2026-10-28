#!/bin/bash
set -euo pipefail

# Refresh packaging/voice-runtime/requirements.lock.txt.
#
#   refresh-lock.sh                     resolve everything to its latest release
#   refresh-lock.sh --keep-locked-pins  keep every version already in the lock and
#                                       resolve only what is new (an additive refresh)
#
# Use --keep-locked-pins when the change is one added dependency: a plain refresh
# also moves every unrelated package to its latest release, which mixes a month of
# upstream churn into a one-line requirement change.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
PYTHON="$PROJECT_ROOT/.build/voice-runtime-base/bin/python3"
LOCK_FILE="packaging/voice-runtime/requirements.lock.txt"

KEEP_LOCKED_PINS=0
for argument in "$@"; do
  case "$argument" in
    --keep-locked-pins) KEEP_LOCKED_PINS=1 ;;
    *) echo "Unknown argument: $argument" >&2; exit 2 ;;
  esac
done

if [[ ! -x "$PYTHON" ]]; then
  echo "Build the base runtime first; missing $PYTHON" >&2
  exit 1
fi

# uv reads the constraint file before it rewrites the output, so the previous lock
# can constrain its own refresh. The lock is read, the resolved file replaces it.
CONSTRAINTS=(--constraint packaging/voice-runtime/constraints-sonoma.txt)
COMPILE_COMMAND="packaging/voice-runtime/refresh-lock.sh"
if [[ "$KEEP_LOCKED_PINS" == "1" && -f "$LOCK_FILE" ]]; then
  CONSTRAINTS+=(--constraint "$LOCK_FILE")
  COMPILE_COMMAND="$COMPILE_COMMAND --keep-locked-pins"
fi

cd "$PROJECT_ROOT"
env \
  UV_CACHE_DIR="$PROJECT_ROOT/.build/uv-cache" \
  UV_CUSTOM_COMPILE_COMMAND="$COMPILE_COMMAND" \
  uv pip compile \
    packaging/voice-runtime/requirements-pilot.txt \
    "${CONSTRAINTS[@]}" \
    --python "$PYTHON" \
    --no-python-downloads \
    --generate-hashes \
    --quiet \
    --output-file "$LOCK_FILE"
