#!/bin/bash
# Build the pinned YuE2 runtime: Python 3.12 + the `mlx-Yue` port + MLX 0.32.2.
#
# **Why this is a third runtime and not an addition to the Music overlay.** `mlx-Yue` pins
# `mlx==0.32.2`; the Voice runtime — and therefore the Music overlay built on top of it — carries
# `mlx==0.31.2`. The port's Metal SDPA kernels and its `mlx_lm` usage are pinned to the version its
# author validated, and one tree cannot hold two `mlx` versions, so YuE2 gets its own interpreter.
# That is the same conclusion the video lane reached with `ltx_pipelines_mlx` (3.11) and `lance-mlx`
# (3.12): the engine a model declares decides the runtime, and the Hub already picks per model.
#
# Why pinned by content: a version string does not distinguish a tree that can run the model from
# one that cannot. `packaging/stage_voice.py` refuses to stage a tree whose digest is not
# `YUE2_RUNTIME_SHA256`, so a package bump is a deliberate act with a new qualification beside it.
#
# Inputs, all required:
#   * a Python 3.12 standalone interpreter — `uv python find 3.12` by default, or
#     `YUE2_BASE_PYTHON=/path/to/bin/python3`;
#   * the pinned `mlx-Yue` checkout at `.build/mlx-yue` (override with `YUE2_SOURCE`), at
#     `YUE2_COMMIT` with a clean tree;
#   * `requirements.yue2.lock.txt` beside this script, hash-checked below. It is a `uv pip freeze`
#     of the environment the two measured YuE2 runs were made in (see
#     `qualification/yue2-music-results-20260927/`), minus the port itself — installed from the
#     checkout, `--no-deps`, so the lock is the only resolver.
#
# The output is deliberately NOT byte-checked against a stored digest here: this script prints it
# (and the *stager* holds the pin), so a rebuild after a package bump prints a new digest and the
# release refuses until a person updates the pin beside the qualification that moved it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
BUILD_ROOT="$PROJECT_ROOT/.build"
YUE2_COMMIT="9253ed133343406947bde7b67d43c7a63fb39d99"
YUE2_REPO="https://github.com/vanch007/mlx-Yue"
REQUIREMENTS="$SCRIPT_DIR/requirements.yue2.lock.txt"
REQUIREMENTS_SHA256="68ede4b42ce684fe3c651cd7bb16219c9963a3894b1a95914347f41d65fbac0b"

OUTPUT_ARGUMENT="${1:-$BUILD_ROOT/yue2-runtime}"
mkdir -p "$BUILD_ROOT"
if [[ "$OUTPUT_ARGUMENT" = /* ]]; then
  OUTPUT_DIR="$OUTPUT_ARGUMENT"
else
  OUTPUT_DIR="$PROJECT_ROOT/$OUTPUT_ARGUMENT"
fi
case "$OUTPUT_DIR" in
  "$PROJECT_ROOT"/*) ;;
  *) echo "Output must stay inside $PROJECT_ROOT" >&2; exit 1 ;;
esac
for command in uv shasum git rsync; do
  command -v "$command" >/dev/null || { echo "Missing build command: $command" >&2; exit 1; }
done
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "Build the YuE2 runtime on Apple Silicon macOS" >&2
  exit 1
fi
printf '%s  %s\n' "$REQUIREMENTS_SHA256" "$REQUIREMENTS" | shasum -a 256 -c -

YUE2_SOURCE="${YUE2_SOURCE:-$BUILD_ROOT/mlx-yue}"
if [[ ! -f "$YUE2_SOURCE/src/lyra/pipeline.py" ]]; then
  cat >&2 <<EOF
The pinned mlx-Yue source is not at $YUE2_SOURCE. Build it once:
  git clone $YUE2_REPO "$YUE2_SOURCE"
  git -C "$YUE2_SOURCE" checkout $YUE2_COMMIT
EOF
  exit 1
fi
FOUND_COMMIT="$(git -C "$YUE2_SOURCE" rev-parse HEAD 2>/dev/null || echo unknown)"
if [[ "$FOUND_COMMIT" != "$YUE2_COMMIT" ]]; then
  echo "The mlx-Yue source at $YUE2_SOURCE is $FOUND_COMMIT, not $YUE2_COMMIT" >&2
  exit 1
fi
if [[ -n "$(git -C "$YUE2_SOURCE" status --porcelain)" ]]; then
  echo "The mlx-Yue checkout has uncommitted changes; only the pinned commit is built" >&2
  exit 1
fi

BASE_PYTHON="${YUE2_BASE_PYTHON:-$(uv python find 3.12 2>/dev/null || true)}"
if [[ -z "$BASE_PYTHON" || ! -x "$BASE_PYTHON" ]]; then
  echo "No Python 3.12 interpreter found; uv python install 3.12, or set YUE2_BASE_PYTHON" >&2
  exit 1
fi

# Never delete: an existing runtime is moved aside inside .build, which is gitignored scratch.
if [[ -e "$OUTPUT_DIR" ]]; then
  MOVED="$OUTPUT_DIR.superseded-$(date -u +%Y%m%d%H%M%S)"
  echo "Moving the existing runtime aside: $MOVED"
  mv "$OUTPUT_DIR" "$MOVED"
fi
mkdir -p "$OUTPUT_DIR"
cp -R "$(dirname "$(dirname "$BASE_PYTHON")")/." "$OUTPUT_DIR/"
# The uv-managed CPython distribution carries a `BUILD` stamp (its own download date) at its root;
# it is build-machine metadata, never runtime content, and `check-build-junk` refuses it below.
find "$OUTPUT_DIR" -maxdepth 1 -name BUILD -delete
PYTHON="$OUTPUT_DIR/bin/python3"
"$PYTHON" -V

uv pip install --break-system-packages --python "$PYTHON" -r "$REQUIREMENTS"
# `--no-deps` because the lock above is the resolution; `--no-editable` so the port is copied in
# rather than left as a .pth pointing at the build checkout — a runtime that works here and dies in
# the app. The port needs its own `vendor/yue` importable, which is why its tree is copied beside
# the environment and named in the smoke check.
uv pip install --break-system-packages --python "$PYTHON" --no-deps --no-editable \
  --no-build-isolation "$YUE2_SOURCE"
find "$OUTPUT_DIR/lib" \( -name direct_url.json -o -name uv_cache.json \) -delete

VENDOR="$OUTPUT_DIR/lib/mlx-yue-source"
mkdir -p "$VENDOR"
rsync -a --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' \
  "$YUE2_SOURCE/" "$VENDOR/"

find "$OUTPUT_DIR" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
find "$OUTPUT_DIR" -depth -type d -name __pycache__ -empty -delete
"$PYTHON" -I -B "$PROJECT_ROOT/packaging/voice-runtime/runtime_packaging.py" normalize-shebangs "$OUTPUT_DIR"
# Reject build-state files before hashing the runtime; the check leaves inputs untouched.
"$PYTHON" -I -B "$PROJECT_ROOT/packaging/voice-runtime/runtime_packaging.py" check-build-junk "$OUTPUT_DIR"
# The shebang rewrite invalidates the hashes uv recorded, and the pre-rewrite scripts embedded the
# absolute build path — so without this the same runtime built at two paths has two digests.
"$PYTHON" -I -B "$PROJECT_ROOT/packaging/video-runtime/normalize_records.py" "$OUTPUT_DIR"
"$PYTHON" -I -B "$SCRIPT_DIR/smoke_imports.py" "$VENDOR"
# The interpreter's generated sysconfig records the prefix it was built at, which is the build
# user's home directory. Neutralise it before the digest is taken, so the shipped tree names nobody;
# `sys.prefix` is derived from the executable's own location, not from this file.
"$PYTHON" -I -B "$SCRIPT_DIR/neutralize_prefix.py" "$OUTPUT_DIR"
"$PYTHON" -I -B -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT/packaging')
from stage_voice import tree_digest
from pathlib import Path
print('YUE2_RUNTIME_SHA256 =', tree_digest(Path('$OUTPUT_DIR')))
"
chmod -R a-w "$OUTPUT_DIR"
echo "content is read-only; a stray import cannot change the digest"
