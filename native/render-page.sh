#!/bin/bash
# Render a page of the app to PNG, offscreen. See tests/native/render_main.swift for why.
#
#   native/render-page.sh [page[,page...]] [output-dir] [WxH ...]
#
# STUDIOHUB_RENDER_LIVE=1 connects the page to the Hub already running on this Mac, so the render
# shows real jobs and history instead of empty lists. Read-only and safe to run while the app is up.
#
# Defaults: audio, /tmp/studiohub-render, 1920x1053 and 980x620 (this Mac's usual window and the
# window's own minimum). Pages are named by the sidebar: overview, audio, subtitle, image, music,
# video, chat, models, voices, jobs, connections, whats-new, settings - and several at once, which
# is how the whole app gets looked at after a change: `all` renders every page.
#
#   native/render-page.sh all /tmp/studiohub-render 1920x1053
set -euo pipefail
ROOT_DIR=$(cd -- "$(dirname -- "$0")/.." && pwd)
PAGE="${1:-audio}"
[ "$PAGE" = "all" ] && PAGE="overview,audio,subtitle,image,music,video,chat,models,voices,jobs,connections,whats-new,settings"
OUT="${2:-/tmp/studiohub-render}"
shift 2 2>/dev/null || true
source "$ROOT_DIR/native/sdk.sh"

# The app has its own entry point, and a build cannot have two. Everything else is compiled as-is,
# so the rendered page is the page that ships rather than a copy of it.
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
cp "$ROOT_DIR/native/"*.swift "$WORK/"
python3 - "$WORK/StudioHub.swift" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
marker = "@main\nstruct StudioHubApplication"
assert marker in text, "the app entry point moved; update render-page.sh"
path.write_text(text.replace(marker, "struct StudioHubApplication", 1))
PY

python3 "$ROOT_DIR/tests/native/render_fixture_patch.py" "$WORK/StudioAPI.swift" "$WORK/StudioViews.swift"

xcrun swiftc -sdk "$SDK_PATH" -target arm64-apple-macos14.0 -parse-as-library \
  "$WORK"/*.swift "$ROOT_DIR/tests/native/render_main.swift" -o "$WORK/render-page"
"$WORK/render-page" "$PAGE" "$OUT" "$@"
