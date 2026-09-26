#!/bin/bash
# Build the pinned Video runtime: Python 3.11 + ltx-2-mlx + MLX 0.32.2 + FastAPI.
#
# Why a separate runtime at all: `ltx_pipelines_mlx` needs Python 3.11 while the shared base
# runtime is 3.12, so the Video worker cannot borrow the Image or Voice tree. Why pinned by
# content: the same rule the Image runtime learned the expensive way — a version string does not
# distinguish a tree that can load the pack from one that cannot, because the mixed 2–8 bit loader
# lives in the *pack*, not in the runtime. What must be exactly right here is the pipeline and MLX,
# so `packaging/stage_voice.py` refuses to stage a tree whose digest is not `VIDEO_RUNTIME_SHA256`.
#
# Inputs, all required:
#   * a Python 3.11 standalone interpreter — `uv python find 3.11.16` by default, or
#     `VIDEO_BASE_PYTHON=/path/to/bin/python3`;
#   * the pinned ltx-2-mlx checkout at `.build/ltx-2-mlx` (override with `VIDEO_LTX_SOURCE`),
#     which must be at LTX_COMMIT;
#   * `requirements.lock.txt` beside this script, hash-checked below.
#
# The output is intentionally NOT byte-hash-checked against a stored digest here: the digest is
# computed by this script and printed, and the *stager* holds the pin. That split is deliberate —
# a runtime rebuilt after a package bump prints a new digest, and the release refuses until a
# person updates the pin beside the qualification that moved it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
BUILD_ROOT="$PROJECT_ROOT/.build"
LTX_COMMIT="3b2e77fd73af5f08961f4436ae979c0579d5deed"
LTX_REPO="https://github.com/dgrauet/ltx-2-mlx"
REQUIREMENTS="$SCRIPT_DIR/requirements.lock.txt"
REQUIREMENTS_SHA256="3aa94a0f1694cc229a339e114e82444373f44d05900029e526eb8a22596432b0"

OUTPUT_ARGUMENT="${1:-$BUILD_ROOT/video-runtime}"
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
for command in uv shasum; do
  command -v "$command" >/dev/null || { echo "Missing build command: $command" >&2; exit 1; }
done
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "Build the Video runtime on Apple Silicon macOS" >&2
  exit 1
fi
printf '%s  %s\n' "$REQUIREMENTS_SHA256" "$REQUIREMENTS" | shasum -a 256 -c -

LTX_SOURCE="${VIDEO_LTX_SOURCE:-$BUILD_ROOT/ltx-2-mlx}"
if [[ ! -d "$LTX_SOURCE/packages/ltx-core-mlx" || ! -d "$LTX_SOURCE/packages/ltx-pipelines-mlx" ]]; then
  cat >&2 <<EOF
The pinned LTX source is not at $LTX_SOURCE. Build it once:
  git clone $LTX_REPO "$LTX_SOURCE"
  git -C "$LTX_SOURCE" checkout $LTX_COMMIT
EOF
  exit 1
fi
FOUND_COMMIT="$(git -C "$LTX_SOURCE" rev-parse HEAD 2>/dev/null || echo unknown)"
if [[ "$FOUND_COMMIT" != "$LTX_COMMIT" ]]; then
  echo "The LTX source at $LTX_SOURCE is $FOUND_COMMIT, not $LTX_COMMIT" >&2
  exit 1
fi

BASE_PYTHON="${VIDEO_BASE_PYTHON:-$(uv python find 3.11.16)}"
if [[ ! -x "$BASE_PYTHON" ]]; then
  echo "No Python 3.11 interpreter at $BASE_PYTHON" >&2
  exit 1
fi

# Never delete: an existing runtime is moved aside inside .build, which is gitignored scratch.
if [[ -e "$OUTPUT_DIR" ]]; then
  MOVED="$OUTPUT_DIR.superseded-$(date -u +%Y%m%d%H%M%S)"
  echo "Moving the existing runtime aside: $MOVED"
  mv "$OUTPUT_DIR" "$MOVED"
fi
mkdir -p "$OUTPUT_DIR"
# The uv-managed standalone tree is relocatable by construction; it is copied rather than linked
# so the result has no path back into the operator's home.
cp -R "$(dirname "$(dirname "$BASE_PYTHON")")/." "$OUTPUT_DIR/"
PYTHON="$OUTPUT_DIR/bin/python3"
"$PYTHON" -V

uv pip install --break-system-packages --python "$PYTHON" -r "$REQUIREMENTS"
# `--no-editable` is load-bearing: uv installs a local *directory* as editable by default, which
# leaves a .pth pointing at this checkout instead of the package inside the runtime — a bundle
# that works on the build machine and dies in the app. The pinned source is copied in whole.
uv pip install --break-system-packages --python "$PYTHON" --no-editable --no-build-isolation \
  "$LTX_SOURCE/packages/ltx-core-mlx" "$LTX_SOURCE/packages/ltx-pipelines-mlx"
# `direct_url.json` records the absolute source path of a local install. It is build-machine
# provenance, not runtime content, and leaving it in makes the content digest machine-specific.
# Matched by name: macOS `find -path` did not match these (pattern semantics), which would have
# left the paths in silently.
find "$OUTPUT_DIR/lib" -name direct_url.json -delete

# Bytecode compiled from the operator's paths is not portable and was never part of the
# qualified tree; the CPython archive ships some, so remove it all and re-check the imports.
# After this the tree is made read-only, which is what keeps it that way: a plain
# `python script.py` run inside the runtime would otherwise write `__pycache__` back in and
# change the content digest the stager pins, and the next release would refuse a runtime that
# had merely been used. The Image runtime's build has done this since it was written.
find "$OUTPUT_DIR" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
find "$OUTPUT_DIR" -depth -type d -name __pycache__ -empty -delete
"$PYTHON" -I -B "$PROJECT_ROOT/packaging/voice-runtime/runtime_packaging.py" normalize-shebangs "$OUTPUT_DIR"
# The shebang rewrite above invalidates the hashes uv recorded, and the pre-rewrite scripts
# embedded the absolute build path — so without this the same runtime built at two paths has two
# different digests. See normalize_records.py.
"$PYTHON" -I -B "$SCRIPT_DIR/normalize_records.py" "$OUTPUT_DIR"
"$PYTHON" -I -B "$PROJECT_ROOT/packaging/video-runtime/smoke_imports.py" \
  --registry "$PROJECT_ROOT/components/video/app/backend/generation.py"
"$PYTHON" -I -B -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT/packaging')
from stage_voice import tree_digest
from pathlib import Path
print('VIDEO_RUNTIME_SHA256 =', tree_digest(Path('$OUTPUT_DIR')))
"
chmod -R a-w "$OUTPUT_DIR"
echo "content is read-only; a stray import cannot change the digest"
