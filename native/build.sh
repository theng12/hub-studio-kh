#!/bin/bash
set -euo pipefail
ROOT_DIR=$(cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$ROOT_DIR/.build"
# Pin the build SDK: the installed 27.0 CLI SDK exposes State macros without its plugin.
# This is a build dependency only; the executable still targets macOS 14.
source "$ROOT_DIR/native/sdk.sh"
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 \
  "$ROOT_DIR/native/ShellCore.swift" "$ROOT_DIR/native/StartupRecovery.swift" "$ROOT_DIR/native/ReleaseNotes.swift" "$ROOT_DIR/tests/native/main.swift" \
  -o "$ROOT_DIR/.build/native-boundary-tests"
"$ROOT_DIR/.build/native-boundary-tests"
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 \
  "$ROOT_DIR/native/ShellCore.swift" "$ROOT_DIR/native/StartupRecovery.swift" "$ROOT_DIR/native/ReleaseNotes.swift" "$ROOT_DIR/native/UpdateClient.swift" "$ROOT_DIR/native/StudioAPI.swift" "$ROOT_DIR/tests/native/api_main.swift" \
  -o "$ROOT_DIR/.build/native-api-tests"
"$ROOT_DIR/.build/native-api-tests"
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 -D LIFECYCLE_TESTING -parse-as-library \
  "$ROOT_DIR/native/ReleaseNotes.swift" "$ROOT_DIR/native/ShellCore.swift" "$ROOT_DIR/native/StartupRecovery.swift" \
  "$ROOT_DIR/native/UpdateClient.swift" "$ROOT_DIR/native/StudioAPI.swift" "$ROOT_DIR/native/Design.swift" "$ROOT_DIR/native/StudioViews.swift" "$ROOT_DIR/native/StudioHub.swift" \
  "$ROOT_DIR/tests/native/secret_checks.swift" "$ROOT_DIR/tests/native/view_checks.swift" "$ROOT_DIR/tests/native/lifecycle_main.swift" -o "$ROOT_DIR/.build/native-lifecycle-tests"
"$ROOT_DIR/.build/native-lifecycle-tests"
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 -O -parse-as-library \
  "$ROOT_DIR"/native/*.swift \
  -o "$ROOT_DIR/.build/StudioHub"
# The background helper, compiled on its own (top-level code, no UI) and staged into the app.
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 -O \
  "$ROOT_DIR/native/helper/HelperMain.swift" \
  -o "$ROOT_DIR/.build/HubStudioHelper"
