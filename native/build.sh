#!/bin/bash
set -euo pipefail
ROOT_DIR=$(cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$ROOT_DIR/.build"
# Pin the build SDK: the installed 27.0 CLI SDK exposes State macros without its plugin.
# This is a build dependency only; the executable still targets macOS 14.
SDK_PATH=$(xcrun --sdk macosx26.5 --show-sdk-path)
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 \
  "$ROOT_DIR/native/ShellCore.swift" "$ROOT_DIR/native/StartupRecovery.swift" "$ROOT_DIR/native/ReleaseNotes.swift" "$ROOT_DIR/tests/native/main.swift" \
  -o "$ROOT_DIR/.build/native-boundary-tests"
"$ROOT_DIR/.build/native-boundary-tests"
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 \
  "$ROOT_DIR/native/ShellCore.swift" "$ROOT_DIR/native/StartupRecovery.swift" "$ROOT_DIR/native/ReleaseNotes.swift" "$ROOT_DIR/native/StudioAPI.swift" "$ROOT_DIR/tests/native/api_main.swift" \
  -o "$ROOT_DIR/.build/native-api-tests"
"$ROOT_DIR/.build/native-api-tests"
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 -O -parse-as-library \
  "$ROOT_DIR"/native/*.swift \
  -o "$ROOT_DIR/.build/StudioHub"
