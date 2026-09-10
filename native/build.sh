#!/bin/bash
set -euo pipefail
ROOT_DIR=$(cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$ROOT_DIR/.build"
xcrun swiftc -target arm64-apple-macos14.0 \
  "$ROOT_DIR/native/ShellCore.swift" "$ROOT_DIR/tests/native/main.swift" \
  -o "$ROOT_DIR/.build/native-boundary-tests"
"$ROOT_DIR/.build/native-boundary-tests"
xcrun swiftc -target arm64-apple-macos14.0 -O -parse-as-library \
  "$ROOT_DIR/native/ShellCore.swift" "$ROOT_DIR/native/StudioHub.swift" \
  -o "$ROOT_DIR/.build/StudioHub"
