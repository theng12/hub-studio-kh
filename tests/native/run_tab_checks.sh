#!/bin/bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "$0")/../.." && pwd)
source "$ROOT_DIR/native/sdk.sh"

echo "Building and running native tab geometry checks..."
mkdir -p "$ROOT_DIR/.build"

xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 \
  "$ROOT_DIR/native/ShellCore.swift" \
  "$ROOT_DIR/native/StartupRecovery.swift" \
  "$ROOT_DIR/native/ReleaseNotes.swift" \
  "$ROOT_DIR/native/UpdateClient.swift" \
  "$ROOT_DIR/native/StudioAPI.swift" \
  "$ROOT_DIR/native/Design.swift" \
  "$ROOT_DIR/tests/native/tab_geometry_checks.swift" \
  -o "$ROOT_DIR/.build/native-tab-geometry-tests"

"$ROOT_DIR/.build/native-tab-geometry-tests"
