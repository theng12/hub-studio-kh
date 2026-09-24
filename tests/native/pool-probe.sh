#!/bin/bash
# What the app reads back from a real controller and a real node, end to end.
#
#   ./tests/native/pool-probe.sh
#
# Starts a packaged controller Hub and a packaged node Hub on loopback ports, joins the node the
# way the app joins one (adopt the Fleet token with the controller's address), then runs the app's
# own workspace store against each end in turn and prints what it decoded.
#
# The Hub's half of this is `tests/test_fleet_pool_visibility.py`. This runs the app's half, which
# is the part a screenshot cannot show: whether the view model actually receives the pool.
set -euo pipefail
ROOT_DIR=$(cd -- "$(dirname -- "$0")/../.." && pwd)
source "$ROOT_DIR/native/sdk.sh"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 -parse-as-library \
  "$ROOT_DIR/native/ShellCore.swift" "$ROOT_DIR/native/StartupRecovery.swift" \
  "$ROOT_DIR/native/ReleaseNotes.swift" "$ROOT_DIR/native/UpdateClient.swift" \
  "$ROOT_DIR/native/StudioAPI.swift" "$ROOT_DIR/tests/native/pool_probe_main.swift" \
  -o "$WORK/pool-probe"

# The Hub fixture the probe talks to. It needs the same Python the Hub runs under; the project's
# own voice runtime carries FastAPI, uvicorn and httpx.
PYTHON="${STUDIOHUB_PYTHON:-$ROOT_DIR/.build/voice-runtime/bin/python3}"
if [ ! -x "$PYTHON" ]; then
  echo "no Hub python at $PYTHON; set STUDIOHUB_PYTHON" >&2
  exit 1
fi

PROBE_BINARY="$WORK/pool-probe" "$PYTHON" "$ROOT_DIR/tests/native/pool_probe_fixture.py"
