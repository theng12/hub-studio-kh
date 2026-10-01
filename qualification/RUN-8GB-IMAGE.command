#!/bin/sh
# Double-click this to run the 8 GB image test (Z-Image-Turbo 4-bit thumbnails, FLUX.2 Klein 4B 4-bit scenes).
#
# Why this file exists: the same reason `RUN-8GB-TTS.command` does. Finder has no application bound to a `.sh`, so
# double-clicking `eight-gb-image-run.sh` opens it in TextEdit and looks broken. This wrapper is the thing to double-click;
# the real measurement stays in its own files so there is one copy of it to keep correct.
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TARGET="$HERE/eight-gb-image-run.sh"
APP="${STUDIOHUB_APP:-/Applications/Studio Hub.app}"

if [ ! -f "$TARGET" ] || [ ! -f "$HERE/eight_gb_image_run.py" ] || [ ! -f "$HERE/ssd_wear.py" ]; then
    echo "The image test is not complete beside this file."
    echo
    echo "Keep RUN-8GB-IMAGE.command, eight-gb-image-run.sh, eight_gb_image_run.py and ssd_wear.py in the same folder -"
    echo "the kit's Technical folder has all four."
    echo
    echo "Looked in: $HERE"
    exit 1
fi

echo "Studio Hub - 8 GB image test"
echo
echo "This measures two image models on this Mac: Z-Image-Turbo 4-bit (thumbnails) and FLUX.2 Klein 4B 4-bit (scenes),"
echo "3 images each at the size Story Studio uses (1280x720), one at a time, through Studio Hub. It does NOT wait for"
echo "memory: it tells Studio Hub to bypass its free-memory guard for each model and just runs it. An image that runs longer"
echo "than 15 minutes is stopped and recorded as 'timed out'; a model that crashes is recorded and the next one goes on."
echo "It also records how much each image writes to this Mac's internal SSD. It writes a results folder beside this file,"
echo "installs nothing and deletes nothing, and puts the Hub's memory setting back at the end (even if you close this"
echo "window). Expect roughly 15 to 60 minutes."
echo
if [ -f "$APP/Contents/Info.plist" ]; then
    VER=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist" 2>/dev/null)
    echo "Studio Hub installed here: version ${VER:-unknown} (this test needs 0.6.125 or later)."
else
    echo "No Studio Hub app found at: $APP"
    echo "Install one from the kit (double-click '1 - Install the app' at the kit's root), or set"
    echo "STUDIOHUB_APP to the app you want measured."
fi
echo

cd "$HERE" || exit 1
bash "$TARGET" "$@"
status=$?

echo
if [ "$status" -eq 0 ]; then
    echo "Finished. Read summary.txt in the results folder named above - or send the whole folder back."
else
    echo "Stopped with status $status. The reason is printed above; send that back."
fi
echo
echo "You can close this window."
