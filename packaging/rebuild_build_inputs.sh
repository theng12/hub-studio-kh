#!/bin/bash
# Rebuild the local gate inputs every Hub gate and release worktree links into `.build/`.
#
# **Why this exists.** A worktree cannot be gated without four things that are gitignored and were
# never recorded anywhere: `docs/tasks/HANDOFF.md` names them as build inputs and says where they
# were linked *from* (the primary checkout), never how they were made. On 2026-11-02 the primary
# checkout's `.build/` went away and the recipe went with it, so every release worktree that links
# `.build/hub-gate` into it lost its interpreter. This script is that recipe, written from the
# evidence still in the tree.
#
# What each input is, and the evidence for it:
#
#   .build/prepare-kit.sh   A copy of the tracked `packaging/prepare-kit.sh`. Worktrees used to
#                           *link* it (HANDOFF 0.6.202-0.6.211); `tests/test_kit_index_prune.py`,
#                           `tests/test_image_8gb_runner.py` and `tests/test_tts_8gb_runner.py`
#                           read it at `.build/prepare-kit.sh` and skip without it, while
#                           `tests/test_model_copy_script.py` reads the tracked one. The copy needs
#                           no edit: the tracked script derives `ROOT` from its own path, which is
#                           what fixed the hard-coded-primary-checkout bug that test asserts.
#
#   .build/test-deps        A `uv pip install --target` tree of pytest and its dependencies, used
#                           as `PYTHONPATH=.build/test-deps` so a runtime that has been stripped of
#                           its tests can still run a pytest stage (HANDOFF 0.6.207: the stripped
#                           Voice runtime carries no pytest, so the voice stage ran through this
#                           path). The five exact versions are the ones
#                           `docs/NEW-COMPUTER-HANDOFF.md` measured out of the old tree.
#
#   .build/hub-gate         The Python 3.12 venv the gate runs *in*: `scripts/hub_offline_gate.py`
#                           launches `unittest` in it and spawns `regressions`/`pytest` with
#                           `bin/python`, and `tests/test_activity_counters.py` reaches for
#                           `bin/python3` beside it. Paths in the 0.6.197/0.6.212 gate logs read
#                           `.build/hub-gate/lib/python3.12/...`, so it is a 3.12 venv, not a
#                           standalone runtime tree.
#
#                           **No freeze of it was ever written down, so its exact contents are
#                           lost.** What the tree proves it held: `fastapi`, `starlette`, `anyio`
#                           and `tqdm` (imported from its site-packages in the 0.6.197-0.6.212 gate
#                           logs); `mlx_lm` (HANDOFF 0.6.101: the wrong venv gave 24
#                           `ModuleNotFoundError`s in `test_chat_worker` because it lacked it);
#                           `espeak` (three espeak failures beside those); and whatever makes the
#                           offline unittest stage green with only 15 skips — which at minimum
#                           includes `mlx` and `PIL`, imported unguarded at module level by
#                           `tests/test_image_staging.py`, and `fastapi`, `httpx`, `starlette`,
#                           `psutil`, `numpy` and `uvicorn` at module level across `tests/` and
#                           `components/hub/`. At ~2 GB it was the size of the Voice lock's
#                           package set, not of a test-only tree.
#
#                           So it is rebuilt from the one complete, hash-pinned third-party set in
#                           this repo that covers that surface — `packaging/voice-runtime/
#                           requirements.lock.txt` — plus the pytest the gate's `regressions` stage
#                           needs and the runtime deliberately strips. That is a reconstruction, not
#                           the original bytes; the freeze this run writes beside the venv is what
#                           makes the *next* rebuild exact.
#
#   .build/yue2-runtime     Rebuilt by the tracked `packaging/yue2-runtime/build.sh`, which pins the
#                           `mlx-Yue` commit, hash-checks its own lock and prints the tree digest
#                           `packaging/stage_voice.py` compares against `YUE2_RUNTIME_SHA256`. The
#                           only thing missing after a cleanup is the source checkout it expects at
#                           `.build/mlx-yue`, so this script clones that at the pinned commit and
#                           then hands over to the tracked builder rather than duplicating it.
#
# Usage:
#   bash packaging/rebuild_build_inputs.sh
#   bash packaging/rebuild_build_inputs.sh --root /path/to/checkout
#   bash packaging/rebuild_build_inputs.sh --only hub-gate,test-deps
#
# Idempotent: an input that already passes its check is left alone and nothing is downloaded. One
# that is present but wrong is moved into `_trash/build-inputs-<stamp>/` first — nothing here
# deletes, and `_trash/` is the owner's to sort out. The pinned trees under
# `~/Developer/_orchestra/_build-inputs/studio-hub-kh/` are never written to; they are inputs to *copy*, not
# to install into.
#
# Network: PyPI and the one `mlx-Yue` git clone. No model weights, no Hub, no generation.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# The checkout this script ships in. Tracked inputs are read from *there*, so the recipe rebuilds
# from the tree it came from even when `--root` names a checkout on another branch; only `.build`
# and `_trash` are written under `--root`.
SOURCE="$(cd "$SCRIPT_DIR/.." && pwd -P)"

ROOT="$SOURCE"
ONLY="prepare-kit,test-deps,hub-gate,yue2-runtime"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      ROOT="$(cd "${2:?--root needs a path}" && pwd -P)"; shift 2 ;;
    --only)
      ONLY="${2:?--only needs a comma-separated list}"; shift 2 ;;
    -h|--help)
      sed -n '2,80p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)
      echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

BUILD="$ROOT/.build"
TRASH="$ROOT/_trash/build-inputs-$(date -u +%Y%m%d%H%M%S)"
STAMPED=0

for command in uv git; do
  command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }
done
[[ -d "$SOURCE/packaging" ]] || { echo "$SOURCE is not a Studio Hub checkout" >&2; exit 1; }

wanted() { [[ ",$ONLY," == *",$1,"* ]]; }

# Nothing is deleted: a stale input joins the stamp folder the same way anything else does.
retire() {
  local path="$1"
  [[ -e "$path" ]] || return 0
  if [[ $STAMPED -eq 0 ]]; then
    mkdir -p "$TRASH"
    STAMPED=1
    echo "Replacing inputs go to $TRASH"
  fi
  echo "  moving $path aside"
  mv "$path" "$TRASH/$(basename "$path")"
}

mkdir -p "$BUILD"

# --- .build/prepare-kit.sh -------------------------------------------------
# Skip only when the copy is present *and* carries the block the tests read out of it.
if wanted prepare-kit; then
  echo "== prepare-kit.sh =="
  if [[ -f "$BUILD/prepare-kit.sh" ]] && grep -q "PRUNEINDEX" "$BUILD/prepare-kit.sh"; then
    echo "  already present and readable by the kit tests"
  else
    retire "$BUILD/prepare-kit.sh"
    cp "$SOURCE/packaging/prepare-kit.sh" "$BUILD/prepare-kit.sh"
    chmod 755 "$BUILD/prepare-kit.sh"
    echo "  copied from packaging/prepare-kit.sh"
  fi
fi

# --- .build/hub-gate -------------------------------------------------------
# Built before test-deps so test-deps can be resolved against the same interpreter the gate uses.
if wanted hub-gate; then
  echo "== hub-gate =="
  gate_ok() {
    [[ -x "$BUILD/hub-gate/bin/python" ]] || return 1
    "$BUILD/hub-gate/bin/python" -c 'import fastapi, httpx, psutil, PIL, mlx, pytest' >/dev/null 2>&1
  }
  if gate_ok; then
    echo "  already present; fastapi/httpx/psutil/PIL/mlx/pytest all import"
  else
    retire "$BUILD/hub-gate"
    uv venv --python 3.12 "$BUILD/hub-gate" >/dev/null
    uv pip install --python "$BUILD/hub-gate/bin/python" --no-progress \
      -r "$SOURCE/packaging/voice-runtime/requirements.lock.txt"
    # The runtime lock deliberately carries no pytest (`strip-tests`); the gate's `regressions`
    # and `pytest` stages are `bin/python -m pytest`, and these are the versions
    # `docs/NEW-COMPUTER-HANDOFF.md` measured in `.build/test-deps`.
    uv pip install --python "$BUILD/hub-gate/bin/python" --no-progress \
      pytest==9.1.1 iniconfig==2.3.0 packaging==26.3 pluggy==1.6.0 Pygments==2.21.0
    uv pip freeze --python "$BUILD/hub-gate/bin/python" \
      > "$BUILD/hub-gate/requirements.freeze.txt"
    echo "  built 3.12 venv; freeze recorded at .build/hub-gate/requirements.freeze.txt"
  fi
fi

# --- .build/test-deps ------------------------------------------------------
# A --target directory, not a venv: it is put on PYTHONPATH beside a runtime that has no pytest.
if wanted test-deps; then
  echo "== test-deps =="
  PY_FOR_TEST_DEPS="${HUB_GATE_PYTHON:-$BUILD/hub-gate/bin/python}"
  if [[ ! -x "$PY_FOR_TEST_DEPS" ]]; then
    PY_FOR_TEST_DEPS="$(uv python find 3.12)"
  fi
  if [[ -f "$BUILD/test-deps/pytest/__init__.py" ]] \
     && "$PY_FOR_TEST_DEPS" -c 'import pytest; assert pytest.__version__ == "9.1.1"' >/dev/null 2>&1; then
    echo "  already present at pytest 9.1.1"
  else
    retire "$BUILD/test-deps"
    # The exact five versions the old tree held (docs/NEW-COMPUTER-HANDOFF.md, [MEASURED]).
    uv pip install --python "$PY_FOR_TEST_DEPS" --target "$BUILD/test-deps" --no-progress \
      pytest==9.1.1 iniconfig==2.3.0 packaging==26.3 pluggy==1.6.0 Pygments==2.21.0
    echo "  installed five packages into .build/test-deps with $PY_FOR_TEST_DEPS"
  fi
fi

# --- .build/yue2-runtime ---------------------------------------------------
# Delegates to the tracked builder; only the pinned source checkout it requires is fetched here.
if wanted yue2-runtime; then
  echo "== yue2-runtime =="
  yue2_ok() { [[ -x "$1/bin/python3" && -d "$1/lib/mlx-yue-source" ]]; }

  # The builder ends with `chmod -R a-w`, and renaming a non-writable directory is refused. The
  # digest does not read permission bits (stage_voice.tree_digest: "Content only, deliberately not
  # the permission bits"), so the top directory is opened for the rename and shut again after it.
  move_runtime() {
    local from="$1" to="$2"
    [[ "$from" == "$to" ]] && return 0
    chmod u+w "$from"
    mv "$from" "$to"
    chmod a-w "$to"
  }

  # The builder resolves its own project root from its own path and refuses an output outside it,
  # so the clone and the build happen under this recipe's checkout and the finished tree is only
  # then moved to `--root`. Same volume, and the digest is path-independent — the builder rewrites
  # shebangs and normalises its records precisely so that is true.
  YUE2_CHECKOUT="$SOURCE/.build"
  BUILT="$YUE2_CHECKOUT/yue2-runtime"
  YUE2_SOURCE="$YUE2_CHECKOUT/mlx-yue"

  if yue2_ok "$BUILD/yue2-runtime"; then
    echo "  already present"
  elif yue2_ok "$BUILT"; then
    # Built on an earlier run that could not move it: relocate rather than pay for it twice.
    retire "$BUILD/yue2-runtime"
    echo "  moving the tree this recipe already built"
    move_runtime "$BUILT" "$BUILD/yue2-runtime"
  else
    YUE2_COMMIT="$(sed -n 's/^YUE2_COMMIT="\([^"]*\)".*/\1/p' "$SOURCE/packaging/yue2-runtime/build.sh")"
    YUE2_REPO="$(sed -n 's/^YUE2_REPO="\([^"]*\)".*/\1/p' "$SOURCE/packaging/yue2-runtime/build.sh")"
    mkdir -p "$YUE2_CHECKOUT"
    if [[ ! -f "$YUE2_SOURCE/src/lyra/pipeline.py" ]]; then
      if [[ -e "$YUE2_SOURCE" ]]; then
        if [[ $STAMPED -eq 0 ]]; then mkdir -p "$TRASH"; STAMPED=1; echo "Replacing inputs go to $TRASH"; fi
        mv "$YUE2_SOURCE" "$TRASH/mlx-yue"
      fi
      git clone --quiet "$YUE2_REPO" "$YUE2_SOURCE"
    fi
    git -C "$YUE2_SOURCE" checkout --quiet "$YUE2_COMMIT"
    echo "  mlx-Yue at $YUE2_COMMIT; handing to packaging/yue2-runtime/build.sh"
    BUILT_LOG="$YUE2_CHECKOUT/yue2-runtime-build.log"
    bash "$SOURCE/packaging/yue2-runtime/build.sh" | tee "$BUILT_LOG"
    retire "$BUILD/yue2-runtime"
    move_runtime "$BUILT" "$BUILD/yue2-runtime"

    # `packaging/stage_voice.py` digests this tree and refuses a staging whose digest is not
    # `YUE2_RUNTIME_SHA256`, so a rebuild that lands anywhere else is a stop, not a warning — and
    # it is cheaper to learn it here than at the end of a release. The lock is hash-pinned and the
    # port is at its pinned commit, so a moved digest is the base interpreter's own tree, and the
    # remedy is the one the stager prints: re-qualify the pin beside it.
    PIN="$(sed -n "s/^YUE2_RUNTIME_SHA256 = '\([0-9a-f]*\)'.*/\1/p" "$SOURCE/packaging/stage_voice.py" | head -1)"
    FOUND="$(sed -n 's/^YUE2_RUNTIME_SHA256 = //p' "$BUILT_LOG" | tail -1)"
    if [[ -n "$PIN" && -n "$FOUND" && "$FOUND" != "$PIN" ]]; then
      echo "  NOTE: the rebuilt tree digests $FOUND, and stage_voice.py pins $PIN." >&2
      echo "        Staging will refuse this tree until the pin is re-qualified against it." >&2
    else
      echo "  digest matches stage_voice.py's pin"
    fi
  fi
fi

echo
echo "Done. Sizes:"
for name in prepare-kit.sh hub-gate test-deps yue2-runtime; do
  [[ -e "$BUILD/$name" ]] && du -sh "$BUILD/$name"
done
