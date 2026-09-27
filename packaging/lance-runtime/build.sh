#!/bin/bash
# Build the pinned Lance runtime: Python 3.12 + lance-mlx + MLX 0.31.2 + mlx-vlm/mlx-video.
#
# **Why this is a second runtime and not an addition to the Video one.** `ltx_pipelines_mlx` needs
# Python 3.11 and `lance-mlx` declares `requires-python = ">=3.12"` — the two engines cannot share
# an interpreter, in either direction. So the lane has two runtimes and two engines, which is what
# "model-agnostic" costs here: the worker picks the interpreter for the engine the model declares,
# exactly as the Hub already picks the worker for the modality.
#
# Why pinned by content: the same rule the Video runtime learned. A version string does not
# distinguish a tree that can load the pack from one that cannot — `mlx-vlm`'s Qwen2.5-VL mRoPE has
# been churning upstream (the pack pins two git commits for exactly that reason), so
# `packaging/stage_voice.py` refuses to stage a tree whose digest is not `LANCE_RUNTIME_SHA256`.
#
# Inputs, all required:
#   * a Python 3.12 standalone interpreter — `uv python find 3.12` by default, or
#     `LANCE_BASE_PYTHON=/path/to/bin/python3`;
#   * the pinned lance-mlx checkout at `.build/lance-mlx` (override with `LANCE_SOURCE`), which must
#     be at LANCE_COMMIT;
#   * `requirements.lance.lock.txt` beside this script, hash-checked below. It is a `uv pip freeze`
#     of the environment the first real Lance run was measured in (see the qualification receipt),
#     minus the local package itself — which is installed from the checkout below, `--no-deps`,
#     so the lock is the only resolver.
#
# The output is deliberately NOT byte-checked against a stored digest here: this script prints it
# (and the *stager* holds the pin), so a rebuild after a package bump prints a new digest and the
# release refuses until a person updates the pin beside the qualification that moved it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
BUILD_ROOT="$PROJECT_ROOT/.build"
LANCE_COMMIT="f8ecc5a86d28b94bd8dc688c200c3e22ffafff4a"
LANCE_REPO="https://github.com/xocialize/lance-mlx"
REQUIREMENTS="$SCRIPT_DIR/requirements.lance.lock.txt"
REQUIREMENTS_SHA256="306ae507ce2eb178264def5c699419d98f84b4d9a89e3de9a306534061d1e82a"

OUTPUT_ARGUMENT="${1:-$BUILD_ROOT/lance-runtime}"
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
for command in uv shasum git; do
  command -v "$command" >/dev/null || { echo "Missing build command: $command" >&2; exit 1; }
done
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "Build the Lance runtime on Apple Silicon macOS" >&2
  exit 1
fi
printf '%s  %s\n' "$REQUIREMENTS_SHA256" "$REQUIREMENTS" | shasum -a 256 -c -

LANCE_SOURCE="${LANCE_SOURCE:-$BUILD_ROOT/lance-mlx}"
if [[ ! -f "$LANCE_SOURCE/src/lance_mlx/pipeline/t2v.py" ]]; then
  cat >&2 <<EOF
The pinned lance-mlx source is not at $LANCE_SOURCE. Build it once:
  git clone $LANCE_REPO "$LANCE_SOURCE"
  git -C "$LANCE_SOURCE" checkout $LANCE_COMMIT
EOF
  exit 1
fi
FOUND_COMMIT="$(git -C "$LANCE_SOURCE" rev-parse HEAD 2>/dev/null || echo unknown)"
if [[ "$FOUND_COMMIT" != "$LANCE_COMMIT" ]]; then
  echo "The lance-mlx source at $LANCE_SOURCE is $FOUND_COMMIT, not $LANCE_COMMIT" >&2
  exit 1
fi

BASE_PYTHON="${LANCE_BASE_PYTHON:-$(uv python find 3.12 2>/dev/null || true)}"
if [[ -z "$BASE_PYTHON" || ! -x "$BASE_PYTHON" ]]; then
  echo "No Python 3.12 interpreter found; uv python install 3.12, or set LANCE_BASE_PYTHON" >&2
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
PYTHON="$OUTPUT_DIR/bin/python3"
"$PYTHON" -V

uv pip install --break-system-packages --python "$PYTHON" -r "$REQUIREMENTS"
# `--no-deps` because the lock above is the resolution: letting the local package resolve again
# would re-fetch the two git dependencies and could move them off their pinned commits.
# `--no-editable` for the same reason the Video runtime states: uv installs a local *directory* as
# editable by default, which leaves a .pth pointing at the build checkout — a runtime that works
# here and dies in the app.
uv pip install --break-system-packages --python "$PYTHON" --no-deps --no-editable \
  --no-build-isolation "$LANCE_SOURCE"
find "$OUTPUT_DIR/lib" -name direct_url.json -delete

find "$OUTPUT_DIR" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
find "$OUTPUT_DIR" -depth -type d -name __pycache__ -empty -delete
"$PYTHON" -I -B "$PROJECT_ROOT/packaging/voice-runtime/runtime_packaging.py" normalize-shebangs "$OUTPUT_DIR"
# The shebang rewrite invalidates the hashes uv recorded, and the pre-rewrite scripts embedded the
# absolute build path — so without this the same runtime built at two paths has two digests.
"$PYTHON" -I -B "$PROJECT_ROOT/packaging/video-runtime/normalize_records.py" "$OUTPUT_DIR"
"$PYTHON" -I -B "$SCRIPT_DIR/smoke_imports.py"
"$PYTHON" -I -B -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT/packaging')
from stage_voice import tree_digest
from pathlib import Path
print('LANCE_RUNTIME_SHA256 =', tree_digest(Path('$OUTPUT_DIR')))
"
chmod -R a-w "$OUTPUT_DIR"
echo "content is read-only; a stray import cannot change the digest"
