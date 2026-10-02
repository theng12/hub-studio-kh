#!/bin/sh
# Double-click this to run the 16 GB Fish test: Fish Audio S2 Pro 4-bit, 8-bit and bf16 on THIS Mac.
#
# Why this file exists: Finder has no application bound to a `.sh`, so the real measurement
# (eight-gb-tts-run.sh, which also runs the 8 GB test) cannot be double-clicked. This wrapper does the
# two things Fish needs that the 8 GB test does not:
#   1. Fish bf16 (and 8-bit) are shelved / 24 GB-tier, so the kit's normal copy step does not put them on a
#      16 GB Mac. import_kit_packages.py puts exactly these three there from the kit's model-library, through
#      the Hub's own import code (every file checked against the manifest), and says what it copied.
#   2. Fish is slower than the other voice models, so every clip gets three times the usual time limit.
# Then it runs the same measurement as the 8 GB test (the Hub's memory guard bypassed for each model and put
# back afterwards, peak memory, "started with N GB free", SSD writes), labelled "16 GB Mac".
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
KIT=$(CDPATH= cd -- "$HERE/.." && pwd)
TARGET="$HERE/eight-gb-tts-run.sh"
IMPORTER="$HERE/import_kit_packages.py"
APP="${STUDIOHUB_APP:-/Applications/Studio Hub.app}"
[ -f "$APP/Contents/Info.plist" ] || APP="$HOME/Applications/Studio Hub.app"
PYTHON="$APP/Contents/Resources/runtimes/voice/bin/python3"
PACKAGES="fish-audio-s2-pro-4bit,fish-audio-s2-pro-8bit,fish-audio-s2-pro-bf16"

for f in "$TARGET" "$IMPORTER" "$HERE/ssd_wear.py" "$HERE/tts-script-S2.txt" "$HERE/tts-script-S4.txt"; do
    if [ ! -f "$f" ]; then
        echo "$(basename "$f") is not beside this file."
        echo
        echo "Keep RUN-16GB-FISH.command, eight-gb-tts-run.sh, import_kit_packages.py, ssd_wear.py, tts-script-S2.txt and"
        echo "tts-script-S4.txt together - the kit's Technical folder has all of them."
        echo
        echo "Looked in: $HERE"
        exit 1
    fi
done

echo "Studio Hub - 16 GB Fish test"
echo
echo "This measures three voice models on this Mac: Fish Audio S2 Pro 4-bit, 8-bit and bf16, each reading the same"
echo "short text in the two owner voices. Fish is slower than the other voices, so a clip gets up to 24 minutes before it is"
echo "stopped and recorded as 'timed out'; a model that crashes is recorded and the next one goes on. It tells Studio Hub to"
echo "bypass its free-memory guard for each model and puts it back at the end (even if you close this window). It records how"
echo "much each clip writes to this Mac's own SSD. Expect roughly 30 to 90 minutes."
echo "(The 3-minute text is off; in Terminal, \"bash RUN-16GB-FISH.command --with-s4\" adds it. Much slower.)"
echo
echo "First it puts the three Fish packages on this Mac from the kit (about 22 GB) if they are not already here, and says"
echo "what it copied. Nothing else is installed and nothing is deleted. It writes a results folder beside this file."
echo
echo "Before you go on: close your other apps, keep the Mac on power, and make sure Studio Hub is open."
echo
if [ -f "$APP/Contents/Info.plist" ]; then
    VER=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist" 2>/dev/null)
    echo "Studio Hub installed here: version ${VER:-unknown} (this test needs 0.6.125 or later)."
else
    echo "No Studio Hub app found at: $APP"
    echo "Install one from the kit (double-click '1 - Install the app' at the kit's root), or set STUDIOHUB_APP to the app you want measured."
    exit 1
fi
echo

# Stop before copying 22 GB if this is not a 16 GB Mac (the measurement would refuse afterwards anyway).
MEM=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
GIB=$((MEM / 1073741824))
case " $* " in
    *" --allow-non-target "*|*" --quick "*) ;;
    *) if [ "$GIB" -lt 15 ] || [ "$GIB" -gt 17 ]; then
           echo "This Mac has $GIB GB of memory, and this test is about 16 GB Macs: on a different-sized Mac the memory numbers"
           echo "would describe this Mac, not a 16 GB one. Nothing was copied. (Terminal: \"bash RUN-16GB-FISH.command --allow-non-target\" runs it anyway.)"
           exit 4
       fi ;;
esac

cd "$HERE" || exit 1
echo "Step 1 of 2 - putting the three Fish packages on this Mac"
echo
"$PYTHON" "$IMPORTER" --app "$APP" --kit "$KIT" --packages "$PACKAGES"
status=$?
if [ "$status" -ne 0 ]; then
    echo
    echo "Stopped before measuring (status $status). The reason is printed above; send that back."
    echo
    echo "You can close this window."
    exit "$status"
fi

echo
echo "Step 2 of 2 - the measurement"
echo
bash "$TARGET" --models fish4,fish8,fishbf16 --target-gb 16 "$@"
status=$?

echo
if [ "$status" -eq 0 ]; then
    echo "Finished. Read summary.txt in the results folder named above - or send the whole folder back."
    echo "Note: Fish bf16 is a shelved model. A Mac that is not marked 'keep all models' moves it into Studio Hub's Trash Folder"
    echo "(kept 15 days) the next time Studio Hub starts - nothing is lost, and that is why this ran straight after the copy."
else
    echo "Stopped with status $status. The reason is printed above; send that back."
fi
echo
echo "You can close this window."
