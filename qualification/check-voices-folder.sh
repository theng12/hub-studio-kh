#!/bin/sh
# Will my existing voices import?  Point this at the folder that holds them.
#
#   sh "/Volumes/KH-STUDIO-ATLAS/Studio Hub App/check-voices-folder.sh" "/path/to/your/voices"
#
# Reads only. Uses the app's own Python, so nothing needs installing.

FOLDER="${1:-}"
APP="${APP:-/Applications/Studio Hub 0.4.0.app}"
PY="$APP/Contents/Resources/runtimes/voice/bin/python3"

if [ -z "$FOLDER" ]; then
  echo "Tell me where your voices are, for example:"
  echo "  sh \"\$0\" \"/Volumes/KH-STUDIO-ATLAS/studio-models/voices\""
  exit 1
fi
if [ ! -d "$FOLDER" ]; then echo "That folder does not exist: $FOLDER"; exit 1; fi
if [ ! -x "$PY" ]; then echo "The app is not installed at $APP"; exit 1; fi

echo "looking in: $FOLDER"
echo
"$PY" - "$FOLDER" <<'PYEOF'
import os, sys
from pathlib import Path

root = Path(sys.argv[1])
AUDIO = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
TEXT = {".txt", ".text", ".srt", ".vtt", ".md"}

def scan(folder):
    clip, text = None, None
    try:
        items = sorted(folder.iterdir())
    except OSError:
        return None, None
    for item in items:
        if item.is_file():
            suffix = item.suffix.lower()
            if suffix in AUDIO and clip is None:
                clip = item
            elif suffix in TEXT and text is None:
                text = item
    return clip, text

entries = [p for p in sorted(root.iterdir()) if not p.name.startswith(".")]
ready = partial = 0
print("folders / files found:", len(entries))
print()
shown = 0
for entry in entries:
    if entry.is_dir():
        clip, text = scan(entry)
        label = entry.name
    else:
        if entry.suffix.lower() not in AUDIO:
            continue
        clip, text = entry, None
        # A transcript may sit beside the clip with the same stem.
        for suffix in TEXT:
            beside = entry.with_suffix(suffix)
            if beside.is_file():
                text = beside
                break
        label = entry.name
    if clip is None:
        state = "no clip"
    elif text is None:
        state = "clip, NO TRANSCRIPT"
        partial += 1
    else:
        state = "ready (clip + transcript)"
        ready += 1
    if shown < 15:
        print("  %-40s %s" % (label[:40], state))
        shown += 1
    elif shown == 15:
        print("  ...")
        shown += 1

print()
print("ready to import:      ", ready)
print("clip but no transcript:", partial, "(these will be skipped - a clone needs the words)")
print()
print("In the app: Voices -> Import Folder..., choose the folder above.")
print("Each voice is added with ownership 'Owned by me' and the consent box ticked.")
PYEOF
