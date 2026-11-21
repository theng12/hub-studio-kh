#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
source "$PROJECT_ROOT/packaging/voice-runtime/pins.env"
BUILD_ROOT="$PROJECT_ROOT/.build"
# Straight at the owner's one trash on the Desktop: there is no `_trash` link in a checkout
# (owner, 2026-11-18: "i hated link folders so it must be point at the direct one instead").
TRASH_ROOT="$HOME/Desktop/_trash/hub-studio-kh/image-runtime-build"
SMOKE_STATES=()
stash_path() {
  local source="$1" base stamp destination suffix
  [[ -e "$source" ]] || return 0
  mkdir -p "$TRASH_ROOT"
  base="$(basename "$source")"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  destination="$TRASH_ROOT/$base.$stamp.$$"
  suffix=0
  while [[ -e "$destination" ]]; do
    suffix=$((suffix + 1))
    destination="$TRASH_ROOT/$base.$stamp.$$.$suffix"
  done
  mv "$source" "$destination"
}
if [[ -d "$PROJECT_ROOT/.build/downloads" ]]; then
  DOWNLOADS="$PROJECT_ROOT/.build/downloads"
else
  DOWNLOADS="$(cd "$PROJECT_ROOT/../.." && pwd -P)/.build/downloads"
fi
OUTPUT_ARGUMENT="${1:-$BUILD_ROOT/image-runtime}"
mkdir -p "$BUILD_ROOT"
if [[ "$OUTPUT_ARGUMENT" = /* ]]; then
  OUTPUT_CANDIDATE="$OUTPUT_ARGUMENT"
else
  OUTPUT_CANDIDATE="$PROJECT_ROOT/$OUTPUT_ARGUMENT"
fi
OUTPUT_BASENAME="$(basename "$OUTPUT_CANDIDATE")"
OUTPUT_PARENT="$(cd "$(dirname "$OUTPUT_CANDIDATE")" && pwd -P)"
OUTPUT_DIR="$OUTPUT_PARENT/$OUTPUT_BASENAME"
case "$OUTPUT_DIR" in
  "$PROJECT_ROOT"/*) ;;
  *) echo "Output must stay inside $PROJECT_ROOT" >&2; exit 1 ;;
esac
if [[ -e "$OUTPUT_DIR" ]]; then
  echo "Refusing to replace an existing runtime: $OUTPUT_DIR" >&2
  exit 1
fi
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "Build Image runtime on Apple Silicon macOS" >&2
  exit 1
fi
for command in shasum tar uv codesign install_name_tool otool lipo; do
  command -v "$command" >/dev/null || { echo "Missing build command: $command" >&2; exit 1; }
done
PYTHON_ARCHIVE="$DOWNLOADS/cpython-${PYTHON_VERSION}+${PYTHON_BUILD_RELEASE}-aarch64-apple-darwin-install_only.tar.gz"
if [[ ! -f "$PYTHON_ARCHIVE" ]]; then
  echo "Pinned CPython archive is unavailable: $PYTHON_ARCHIVE" >&2
  exit 1
fi
printf '%s  %s\n' "$PYTHON_ARCHIVE_SHA256" "$PYTHON_ARCHIVE" | shasum -a 256 -c -
printf '%s  %s\n' 'cd8cd947284505f1b0f1e9c013ba314a277e83970319a40525cb275da5105e4c' \
  "$SCRIPT_DIR/requirements.lock.txt" | shasum -a 256 -c -
STAGE="$(mktemp -d "$BUILD_ROOT/image-runtime-stage.XXXXXX")"
RELOCATION_PARENT="$BUILD_ROOT/Relocation Check $$"
cleanup() {
  stash_path "$STAGE"
  stash_path "$RELOCATION_PARENT"
  local state
  # `SMOKE_STATES` is empty until the smoke phase, and this Mac's bash is 3.2, where `set -u` makes
  # an empty array expansion fatal — so the EXIT trap itself crashed and hid the real error
  # (measured 2026-11-18: the build died with "SMOKE_STATES[@]: unbound variable").
  for state in ${SMOKE_STATES[@]+"${SMOKE_STATES[@]}"}; do
    stash_path "$state"
  done
}
trap cleanup EXIT
tar -xzf "$PYTHON_ARCHIVE" -C "$STAGE" --strip-components=1
env MACOSX_DEPLOYMENT_TARGET=14.0 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  uv pip sync --python "$STAGE/bin/python3" --system --offline \
    --no-compile-bytecode --no-python-downloads "$SCRIPT_DIR/requirements.lock.txt"
mkdir -p "$STAGE/manifest" "$STAGE/licenses"
cp "$SCRIPT_DIR/requirements.lock.txt" "$STAGE/manifest/requirements.lock.txt"
cp "$SCRIPT_DIR/smoke_imports.py" "$STAGE/manifest/smoke_imports.py"
cp "$STAGE/lib/python3.12/LICENSE.txt" "$STAGE/licenses/CPython-LICENSE.txt"
cp -R "$SCRIPT_DIR/licenses/." "$STAGE/licenses/"
"$STAGE/bin/python3" -I -B "$PROJECT_ROOT/packaging/voice-runtime/inventory_packages.py" \
  "$STAGE/manifest/python-distributions.json" "$STAGE/licenses/python"
"$STAGE/bin/python3" -I -B "$PROJECT_ROOT/packaging/voice-runtime/runtime_packaging.py" normalize-shebangs "$STAGE"
"$STAGE/bin/python3" -I -B "$PROJECT_ROOT/packaging/voice-runtime/runtime_packaging.py" sanitize-rpaths "$STAGE"
CACHE_TRASH="$TRASH_ROOT/generated-cache.$(date -u +%Y%m%dT%H%M%SZ).$$"
while IFS= read -r -d '' cache; do
  relative="${cache#"$STAGE"/}"
  mkdir -p "$CACHE_TRASH/$(dirname "$relative")"
  mv "$cache" "$CACHE_TRASH/$relative"
done < <(find "$STAGE" -type f \( -name '*.pyc' -o -name '*.pyo' \) -print0)
while IFS= read -r -d '' cache; do
  relative="${cache#"$STAGE"/}"
  mkdir -p "$CACHE_TRASH/$(dirname "$relative")"
  mv "$cache" "$CACHE_TRASH/$relative"
done < <(find "$STAGE" -depth -type d -name __pycache__ -empty -print0)
"$STAGE/bin/python3" -I -B "$PROJECT_ROOT/packaging/voice-runtime/runtime_packaging.py" prune-build-junk "$STAGE"
# After the shebang rewrite and the junk prune, so the RECORDs describe the final tree and the
# digest is a content identity rather than a build-path identity (see normalize_record_files).
"$STAGE/bin/python3" -I -B "$PROJECT_ROOT/packaging/voice-runtime/runtime_packaging.py" normalize-records "$STAGE"
"$STAGE/bin/python3" -I -B "$PROJECT_ROOT/packaging/voice-runtime/runtime_packaging.py" audit "$STAGE" \
  --target-macos 14.0 --forbid-prefix "$PROJECT_ROOT" --forbid-prefix "$STAGE"
chmod -R a-w "$STAGE"
chmod u+w "$STAGE/manifest"
"$STAGE/bin/python3" -I -B "$PROJECT_ROOT/packaging/voice-runtime/runtime_packaging.py" manifest "$STAGE"
chmod a-w "$STAGE/manifest/runtime-files.json" "$STAGE/manifest"
"$STAGE/bin/python3" -I -B "$SCRIPT_DIR/append_mlx_serve.py" "$STAGE" \
  "$DOWNLOADS/mlx-serve-bin-macos-arm64.tar.gz"
run_smoke() {
  local runtime="$1" state
  state="$(mktemp -d "$BUILD_ROOT/image-runtime-state.XXXXXX")"
  SMOKE_STATES+=("$state")
  mkdir -p "$state/home" "$state/tmp"
  # `--registry` names the Image worker's own model registry, because this file is executed from
  # a copy inside the runtime and cannot find the repository on its own. The smoke imports every
  # class that registry lists, so a model the worker can load cannot be missing from the runtime
  # without failing here.
  (cd /; env -i HOME="$state/home" TMPDIR="$state/tmp" PATH=/usr/bin:/bin \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 \
    "$runtime/bin/python3" -I -B "$runtime/manifest/smoke_imports.py" \
    --registry "$PROJECT_ROOT/components/image/app/backend/generation.py")
  "$runtime/bin/python3" -I -B "$PROJECT_ROOT/packaging/voice-runtime/runtime_packaging.py" verify "$runtime"
  stash_path "$state"
}
mkdir -p "$RELOCATION_PARENT"
RELOCATED="$RELOCATION_PARENT/Image Runtime"
chmod u+w "$STAGE"
mv "$STAGE" "$RELOCATED"
chmod a-w "$RELOCATED"
run_smoke "$RELOCATED"
chmod u+w "$RELOCATED"
mv "$RELOCATED" "$OUTPUT_DIR"
chmod a-w "$OUTPUT_DIR"
run_smoke "$OUTPUT_DIR"
trap - EXIT
stash_path "$RELOCATION_PARENT"
echo "Image runtime ready: $OUTPUT_DIR"
