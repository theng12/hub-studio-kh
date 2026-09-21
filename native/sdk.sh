if [ "${STUDIOHUB_MACOS_SDK+x}" = x ]; then
  SDK_SELECTOR=$STUDIOHUB_MACOS_SDK
else
  SDK_SELECTOR=macosx26.5
fi

if [ -z "$SDK_SELECTOR" ]; then
  printf 'Requested macOS SDK is empty. Set STUDIOHUB_MACOS_SDK to an installed xcrun SDK selector (for example macosx26.5).\n' >&2
  exit 64
fi

if ! SDK_PATH=$(xcrun --sdk "$SDK_SELECTOR" --show-sdk-path); then
  printf 'Requested macOS SDK "%s" is unavailable. Set STUDIOHUB_MACOS_SDK to an installed xcrun SDK selector (for example macosx26.5).\n' "$SDK_SELECTOR" >&2
  exit 1
fi
