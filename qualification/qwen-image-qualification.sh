#!/bin/sh
# Portable launcher for the physical Qwen Image 2.1 4-bit SSD qualification.
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
KIT=$(CDPATH= cd -- "$HERE/.." && pwd)
TIER=""
if [ "${1:-}" = "--tier" ] && [ -n "${2:-}" ]; then
    TIER="$2"
    shift 2
fi
case "$TIER" in
    16|24|32) ;;
    *) echo "Use --tier 16 or --tier 24 (Turbo also accepts --tier 32). The 8 GB tier is unsupported." >&2; exit 4 ;;
esac

for file in qwen_image_qualification.py library_guard.py ssd_wear.py; do
    if [ ! -f "$HERE/$file" ]; then
        echo "NOT MEASURED: $file is not beside this launcher in the kit's Technical folder." >&2
        exit 3
    fi
done

APP="${STUDIOHUB_APP:-}"
for candidate in "$APP" "$KIT/Hub Studio KH.app" "/Applications/Hub Studio KH.app" "$HOME/Applications/Hub Studio KH.app"; do
    if [ -n "$candidate" ] && [ -x "$candidate/Contents/Resources/runtimes/image/bin/python3" ] && [ -x "$candidate/Contents/Resources/runtimes/voice/bin/python3" ]; then
        APP="$candidate"
        break
    fi
done
if [ -z "$APP" ] || [ ! -x "$APP/Contents/Resources/runtimes/image/bin/python3" ] || [ ! -x "$APP/Contents/Resources/runtimes/voice/bin/python3" ]; then
    echo "NOT MEASURED: no complete Hub Studio KH.app with Image and Voice runtimes was found." >&2
    exit 3
fi

exec "$APP/Contents/Resources/runtimes/voice/bin/python3" \
    "$HERE/qwen_image_qualification.py" --tier "$TIER" --kit "$KIT" --app "$APP" "$@"
