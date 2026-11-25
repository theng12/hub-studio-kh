#!/bin/bash
set -euo pipefail
ROOT_DIR=$(cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$ROOT_DIR/.build"
# Pin the build SDK: the installed 27.0 CLI SDK exposes State macros without its plugin.
# This is a build dependency only; the executable still targets macOS 14.
source "$ROOT_DIR/native/sdk.sh"
# The Sparkle shim is part of the app: a build without the vendored library, C header, and
# Sparkle.framework refuses rather than shipping an app silently missing its updater.
SPARKLE_DIR="${KH_SPARKLE_DIR:-$ROOT_DIR/vendor/khsparkle}"
for piece in "$SPARKLE_DIR/libkhsparkle.a" "$SPARKLE_DIR/khsparkle.h" "$SPARKLE_DIR/Sparkle.framework"; do
  if [ ! -e "$piece" ]; then
    echo "ERROR: vendored Sparkle piece missing: $piece" >&2
    exit 2
  fi
done
SPARKLE_FLAGS=(
  -F "$SPARKLE_DIR"
  -framework Sparkle
  -L "$SPARKLE_DIR" -lkhsparkle
  -import-objc-header "$SPARKLE_DIR/khsparkle.h"
  -Xlinker -rpath -Xlinker @executable_path/../Frameworks
)
# The background helper, compiled on its own (top-level code, no UI) and staged into the app.
# Built before the test binaries: the boundary tests run it against a synthetic bundle layout.
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 -O \
  "$ROOT_DIR/native/helper/HelperMain.swift" \
  -o "$ROOT_DIR/.build/HubStudioHelper"
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 \
  "$ROOT_DIR/native/ShellCore.swift" "$ROOT_DIR/native/StartupRecovery.swift" "$ROOT_DIR/native/ReleaseNotes.swift" "$ROOT_DIR/tests/native/main.swift" \
  -o "$ROOT_DIR/.build/native-boundary-tests"
"$ROOT_DIR/.build/native-boundary-tests"
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 \
  "$ROOT_DIR/native/ShellCore.swift" "$ROOT_DIR/native/StartupRecovery.swift" "$ROOT_DIR/native/ReleaseNotes.swift" "$ROOT_DIR/native/StudioAPI.swift" "$ROOT_DIR/tests/native/api_main.swift" \
  -o "$ROOT_DIR/.build/native-api-tests"
"$ROOT_DIR/.build/native-api-tests"
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 -D LIFECYCLE_TESTING -parse-as-library \
  "${SPARKLE_FLAGS[@]}" \
  "$ROOT_DIR/native/ReleaseNotes.swift" "$ROOT_DIR/native/ShellCore.swift" "$ROOT_DIR/native/StartupRecovery.swift" \
  "$ROOT_DIR/native/StudioAPI.swift" "$ROOT_DIR/native/Design.swift" "$ROOT_DIR/native/StudioViews.swift" "$ROOT_DIR/native/StudioHub.swift" \
  "$ROOT_DIR/tests/native/secret_checks.swift" "$ROOT_DIR/tests/native/view_checks.swift" "$ROOT_DIR/tests/native/lifecycle_main.swift" -o "$ROOT_DIR/.build/native-lifecycle-tests"
# Outside a .app bundle, dyld resolves @rpath/Sparkle.framework via DYLD_FRAMEWORK_PATH (INTEGRATION.md §3B).
DYLD_FRAMEWORK_PATH="$SPARKLE_DIR" "$ROOT_DIR/.build/native-lifecycle-tests"
xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 -O -parse-as-library \
  "${SPARKLE_FLAGS[@]}" \
  "$ROOT_DIR"/native/*.swift \
  -o "$ROOT_DIR/.build/Hub Studio KH"
