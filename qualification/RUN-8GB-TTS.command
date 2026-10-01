#!/bin/sh
# Double-click this to run the 8 GB text-to-speech test (Qwen3-TTS 0.6B 8-bit, VoxCPM2 4-bit, OmniVoice bf16).
#
# Why this file exists: the same reason `RUN-8GB-Z-IMAGE.command` does. Finder has no application
# bound to a `.sh`, so double-clicking `eight-gb-tts-run.sh` opens it in TextEdit and looks broken.
# This wrapper is the thing to double-click; the real measurement stays in its own file so there is
# one copy of it to keep correct.
#
# Before it runs anything it says plainly which Studio Hub is installed here. The measurement itself
# then checks the rest (memory size, the three models, the two voices, a quiet Hub) and refuses in
# plain words rather than guessing.
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TARGET="$HERE/eight-gb-tts-run.sh"
APP="${STUDIOHUB_APP:-/Applications/Studio Hub.app}"

if [ ! -f "$TARGET" ]; then
    echo "eight-gb-tts-run.sh is not beside this file."
    echo
    echo "Keep RUN-8GB-TTS.command, eight-gb-tts-run.sh, tts-script-S2.txt and tts-script-S4.txt in the"
    echo "same folder - the kit's Technical folder has all four."
    echo
    echo "Looked in: $HERE"
    exit 1
fi

echo "Studio Hub - 8 GB text-to-speech test"
echo
echo "This measures three voice models on this Mac: Qwen3-TTS 0.6B 8-bit, VoxCPM2 4-bit and OmniVoice"
echo "bf16, each reading the same two texts in the two owner voices. It takes one to a few hours on an"
echo "8 GB Mac, writes a results folder beside this file, installs nothing and deletes nothing. It asks"
echo "Studio Hub to let idle models go before each model; if a model is held back for lack of free memory it"
echo "lowers that one model's memory requirement temporarily to measure it (with a safety stop) and puts it back."
echo
echo "Before you go on: close your other apps, keep the Mac on power, and make sure Studio Hub is open."
echo
if [ -f "$APP/Contents/Info.plist" ]; then
    VER=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist" 2>/dev/null)
    echo "Studio Hub installed here: version ${VER:-unknown} (this test needs 0.6.122 or later)."
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
