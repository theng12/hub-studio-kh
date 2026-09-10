#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
source "$SCRIPT_DIR/pins.env"
PYTHON_MINOR="${PYTHON_VERSION%.*}"

BUILD_ROOT="$PROJECT_ROOT/.build"
DOWNLOADS="$BUILD_ROOT/downloads"
UV_CACHE="$BUILD_ROOT/uv-cache"
LOG="$BUILD_ROOT/voice-runtime-build.log"
OUTPUT_ARGUMENT="${1:-$BUILD_ROOT/voice-runtime}"
mkdir -p "$BUILD_ROOT" "$DOWNLOADS" "$UV_CACHE"

if [[ "$OUTPUT_ARGUMENT" = /* ]]; then
  OUTPUT_CANDIDATE="$OUTPUT_ARGUMENT"
else
  OUTPUT_CANDIDATE="$PROJECT_ROOT/$OUTPUT_ARGUMENT"
fi
OUTPUT_BASENAME="$(basename "$OUTPUT_CANDIDATE")"
if [[ "$OUTPUT_BASENAME" == "." || "$OUTPUT_BASENAME" == ".." ]]; then
  echo "Output must name a new directory" >&2
  exit 1
fi
if ! OUTPUT_PARENT="$(cd "$(dirname "$OUTPUT_CANDIDATE")" 2>/dev/null && pwd -P)"; then
  echo "Output parent must already exist: $(dirname "$OUTPUT_CANDIDATE")" >&2
  exit 1
fi
OUTPUT_DIR="$OUTPUT_PARENT/$OUTPUT_BASENAME"
case "$OUTPUT_DIR" in
  "$PROJECT_ROOT"/*) ;;
  *) echo "Output must stay inside $PROJECT_ROOT" >&2; exit 1 ;;
esac
if [[ -e "$OUTPUT_DIR" ]]; then
  echo "Refusing to overwrite existing output: $OUTPUT_DIR" >&2
  exit 1
fi

exec > >(tee "$LOG") 2>&1

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "Voice runtime must be built on Apple Silicon macOS" >&2
  exit 1
fi
for command in codesign curl make clang install_name_tool shasum strip tar uv xz; do
  command -v "$command" >/dev/null || { echo "Missing build command: $command" >&2; exit 1; }
done

download() {
  local url="$1" destination="$2" expected="$3"
  if [[ ! -f "$destination" ]]; then
    local partial="${destination}.partial.$$"
    curl -fL --retry 3 --output "$partial" "$url"
    mv "$partial" "$destination"
  fi
  printf '%s  %s\n' "$expected" "$destination" | shasum -a 256 -c -
}

PYTHON_ARCHIVE="$DOWNLOADS/cpython-${PYTHON_VERSION}+${PYTHON_BUILD_RELEASE}-aarch64-apple-darwin-install_only.tar.gz"
FFMPEG_ARCHIVE="$DOWNLOADS/ffmpeg-${FFMPEG_VERSION}.tar.xz"
OPEN_JTALK_DICTIONARY_ARCHIVE="$DOWNLOADS/open_jtalk_dic_utf_8-${OPEN_JTALK_DICTIONARY_VERSION}.tar.gz"
download "$PYTHON_ARCHIVE_URL" "$PYTHON_ARCHIVE" "$PYTHON_ARCHIVE_SHA256"
download "$FFMPEG_SOURCE_URL" "$FFMPEG_ARCHIVE" "$FFMPEG_SOURCE_SHA256"
download "$OPEN_JTALK_DICTIONARY_URL" "$OPEN_JTALK_DICTIONARY_ARCHIVE" "$OPEN_JTALK_DICTIONARY_SHA256"

STAGE="$(mktemp -d "$BUILD_ROOT/voice-runtime-stage.XXXXXX")"
FFMPEG_BUILD="$(mktemp -d "$BUILD_ROOT/ffmpeg-build.XXXXXX")"
RELOCATION_PARENT="$BUILD_ROOT/Relocation Check $$"
cleanup() {
  [[ ! -d "$STAGE" ]] || rm -rf "$STAGE"
  [[ ! -d "$FFMPEG_BUILD" ]] || rm -rf "$FFMPEG_BUILD"
  [[ ! -d "$RELOCATION_PARENT" ]] || rm -rf "$RELOCATION_PARENT"
}
trap cleanup EXIT

echo "Extracting CPython ${PYTHON_VERSION}+${PYTHON_BUILD_RELEASE}"
tar -xzf "$PYTHON_ARCHIVE" -C "$STAGE" --strip-components=1

echo "Installing the exact Voice runtime lock"
env \
  MACOSX_DEPLOYMENT_TARGET="$MACOSX_DEPLOYMENT_TARGET" \
  CFLAGS="-g0 -ffile-prefix-map=$PROJECT_ROOT=." \
  CXXFLAGS="-g0 -ffile-prefix-map=$PROJECT_ROOT=." \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONNOUSERSITE=1 \
  UV_CACHE_DIR="$UV_CACHE" \
  uv pip sync \
    --python "$STAGE/bin/python3" \
    --system \
    --no-compile-bytecode \
    --no-python-downloads \
  "$SCRIPT_DIR/requirements.lock.txt"

echo "Installing the pinned Open JTalk dictionary"
PYOPENJTALK_PACKAGE="$STAGE/lib/python${PYTHON_MINOR}/site-packages/pyopenjtalk"
test -d "$PYOPENJTALK_PACKAGE"
tar -xzf "$OPEN_JTALK_DICTIONARY_ARCHIVE" -C "$PYOPENJTALK_PACKAGE"
while IFS= read -r extension; do
  strip -S "$extension"
  codesign --force --sign - "$extension"
done < <(find "$PYOPENJTALK_PACKAGE" -maxdepth 1 -type f -name '*.so' -print)
env PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  "$STAGE/bin/python3" -I -B -c \
  'import pyopenjtalk; assert pyopenjtalk.g2p("こんにちは").strip()'

echo "Building FFmpeg ${FFMPEG_VERSION} on the build host"
tar -xJf "$FFMPEG_ARCHIVE" -C "$FFMPEG_BUILD"
FFMPEG_SOURCE="$FFMPEG_BUILD/ffmpeg-${FFMPEG_VERSION}"
FFMPEG_DESTDIR="$FFMPEG_BUILD/install"
(
  cd "$FFMPEG_SOURCE"
  env MACOSX_DEPLOYMENT_TARGET="$MACOSX_DEPLOYMENT_TARGET" ./configure \
    --prefix=/ \
    --disable-shared \
    --enable-static \
    --disable-autodetect \
    --disable-doc \
    --disable-debug \
    --disable-network \
    --disable-ffplay
  make -j "$(sysctl -n hw.logicalcpu)"
  make DESTDIR="$FFMPEG_DESTDIR" install
)
install -m 0755 "$FFMPEG_DESTDIR/bin/ffmpeg" "$STAGE/bin/ffmpeg"
install -m 0755 "$FFMPEG_DESTDIR/bin/ffprobe" "$STAGE/bin/ffprobe"
mkdir -p "$STAGE/licenses" "$STAGE/manifest"
install -m 0644 "$FFMPEG_SOURCE/LICENSE.md" "$STAGE/licenses/FFmpeg-LICENSE.md"
install -m 0644 "$STAGE/lib/python${PYTHON_MINOR}/LICENSE.txt" "$STAGE/licenses/CPython-LICENSE.txt"
install -m 0644 \
  "$PYOPENJTALK_PACKAGE/open_jtalk_dic_utf_8-${OPEN_JTALK_DICTIONARY_VERSION}/COPYING" \
  "$STAGE/licenses/Open-JTalk-dictionary-COPYING.txt"
install -m 0644 "$PYOPENJTALK_PACKAGE/htsvoice/LICENSE_mei_normal.htsvoice" \
  "$STAGE/licenses/HTS-Voice-Mei-LICENSE.txt"

cp "$SCRIPT_DIR/requirements.lock.txt" "$STAGE/manifest/requirements.lock.txt"
cp "$SCRIPT_DIR/smoke_imports.py" "$STAGE/manifest/smoke_imports.py"
cp "$SCRIPT_DIR/sitecustomize.py" "$STAGE/lib/python${PYTHON_MINOR}/site-packages/sitecustomize.py"
"$STAGE/bin/python3" -I -B "$SCRIPT_DIR/inventory_packages.py" \
  "$STAGE/manifest/python-distributions.json" \
  "$STAGE/licenses/python"
"$STAGE/bin/python3" -I -B \
  "$SCRIPT_DIR/runtime_packaging.py" normalize-shebangs "$STAGE"
"$STAGE/bin/python3" -I -B \
  "$SCRIPT_DIR/runtime_packaging.py" sanitize-rpaths "$STAGE"

find "$STAGE" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
find "$STAGE" -depth -type d -name __pycache__ -empty -delete

"$STAGE/bin/python3" -I -B \
  "$SCRIPT_DIR/runtime_packaging.py" audit "$STAGE" \
  --target-macos "$MACOSX_DEPLOYMENT_TARGET" \
  --forbid-prefix "$PROJECT_ROOT" \
  --forbid-prefix "$STAGE" \
  --forbid-prefix "$FFMPEG_BUILD"
chmod -R a-w "$STAGE"
chmod u+w "$STAGE/manifest"
"$STAGE/bin/python3" -I -B \
  "$SCRIPT_DIR/runtime_packaging.py" manifest "$STAGE"
chmod a-w "$STAGE/manifest/runtime-files.json" "$STAGE/manifest"

run_smoke() {
  local runtime="$1" external
  external="$(mktemp -d "$BUILD_ROOT/voice-runtime-state.XXXXXX")"
  mkdir -p "$external/home" "$external/tmp"
  (
    cd /
    env -i \
      HOME="$external/home" \
      TMPDIR="$external/tmp" \
      PATH=/usr/bin:/bin \
      PYTHONDONTWRITEBYTECODE=1 \
      PYTHONNOUSERSITE=1 \
      "$runtime/bin/python3" -I -B "$runtime/manifest/smoke_imports.py"
    "$runtime/bin/ffmpeg" -version | head -1
    "$runtime/bin/ffprobe" -version | head -1
  )
  "$runtime/bin/python3" -I -B \
    "$SCRIPT_DIR/runtime_packaging.py" verify "$runtime"
  rm -rf "$external"
}

mkdir -p "$RELOCATION_PARENT"
RELOCATED="$RELOCATION_PARENT/Voice Runtime"
chmod u+w "$STAGE"
mv "$STAGE" "$RELOCATED"
chmod a-w "$RELOCATED"
run_smoke "$RELOCATED"
mkdir -p "$(dirname "$OUTPUT_DIR")"
chmod u+w "$RELOCATED"
mv "$RELOCATED" "$OUTPUT_DIR"
chmod a-w "$OUTPUT_DIR"
run_smoke "$OUTPUT_DIR"

trap - EXIT
[[ ! -d "$FFMPEG_BUILD" ]] || rm -rf "$FFMPEG_BUILD"
[[ ! -d "$RELOCATION_PARENT" ]] || rm -rf "$RELOCATION_PARENT"
echo "Voice runtime ready: $OUTPUT_DIR"
