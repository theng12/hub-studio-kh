#!/bin/sh
# Double-click this to run the 8 GB Breeze test: Breeze TTS 2 4-bit (English voice) on THIS 8 GB Mac.
#
# Why this file exists: Finder has no application bound to a `.sh`, so the real measurement
# (eight-gb-tts-run.sh, the same one the 8 GB text-to-speech test uses) cannot be double-clicked. This
# wrapper does the one thing Breeze needs that the 8 GB test does not:
#   1. Breeze is a 16 GB-tier model, so the kit's normal copy step ("2 - Copy the models") does not put it on
#      an 8 GB Mac. import_kit_packages.py puts exactly this one package there from the kit's model-library,
#      through the Hub's own import code (every file checked against the manifest), and says what it copied.
# Then it runs the measurement for Breeze only (--models breeze): the Hub's memory guard bypassed for Breeze and
# put back afterwards, the calibrated Breeze sampling sent with every clip, peak memory, swap, memory pressure,
# the Hub's own telemetry for the job, the section checks, SSD writes. Nothing about the product changes.
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
KIT=$(CDPATH= cd -- "$HERE/.." && pwd)
TARGET="$HERE/eight-gb-tts-run.sh"
IMPORTER="$HERE/import_kit_packages.py"
APP="${STUDIOHUB_APP:-/Applications/Studio Hub.app}"
[ -f "$APP/Contents/Info.plist" ] || APP="$HOME/Applications/Studio Hub.app"
PYTHON="$APP/Contents/Resources/runtimes/voice/bin/python3"
PACKAGES="breeze-tts-2-mlx-4bit"

for f in "$TARGET" "$IMPORTER" "$HERE/ssd_wear.py" "$HERE/tts-script-S2.txt" "$HERE/tts-script-S4.txt"; do
    if [ ! -f "$f" ]; then
        echo "$(basename "$f") is not beside this file."
        echo
        echo "Keep RUN-8GB-BREEZE.command, eight-gb-tts-run.sh, import_kit_packages.py, ssd_wear.py, tts-script-S2.txt and"
        echo "tts-script-S4.txt together - the kit's Technical folder has all of them."
        echo
        echo "Looked in: $HERE"
        exit 1
    fi
done

echo "Studio Hub - 8 GB Breeze test"
echo
echo "This measures one voice model on this Mac: Breeze TTS 2 4-bit, reading the same short English text (about 175 words)"
echo "in the two owner voices, with the calibrated Breeze settings. It tells Studio Hub to bypass its memory guard for Breeze"
echo "and puts it back at the end (even if you close this window). A clip gets up to 20 minutes before it is stopped and"
echo "recorded as 'timed out'; if Breeze crashes, that is recorded too. It records peak memory, swap, memory pressure and how"
echo "much each clip writes to this Mac's own SSD. Expect roughly 15 to 40 minutes."
echo
echo "First it puts the Breeze package on this Mac from the kit (about 3 GB) if it is not already here, and says what it"
echo "copied. Nothing else is installed and nothing is deleted. It writes a results folder beside this file."
echo
echo "Before you go on: close your other apps, keep the Mac on power, and make sure Studio Hub is open."
echo
if [ -f "$APP/Contents/Info.plist" ]; then
    VER=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist" 2>/dev/null)
    echo "Studio Hub installed here: version ${VER:-unknown} (this test needs 0.6.142 or later for the Breeze settings)."
else
    echo "No Studio Hub app found at: $APP"
    echo "Install one from the kit (double-click '1 - Install the app' at the kit's root), or set STUDIOHUB_APP to the app you want measured."
    exit 1
fi
echo

# Stop before copying if this is not an 8 GB Mac (the measurement would refuse afterwards anyway).
MEM=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
GIB=$((MEM / 1073741824))
case " $* " in
    *" --allow-non-target "*|*" --quick "*) ;;
    *) if [ "$GIB" -lt 7 ] || [ "$GIB" -gt 9 ]; then
           echo "This Mac has $GIB GB of memory, and this test is about 8 GB Macs: on a different-sized Mac the memory numbers"
           echo "would describe this Mac, not an 8 GB one. Nothing was copied. (Terminal: \"bash RUN-8GB-BREEZE.command --allow-non-target\" runs it anyway.)"
           exit 4
       fi ;;
esac

cd "$HERE" || exit 1
echo "Step 1 of 2 - putting the Breeze package on this Mac"
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
# 480 s x 2.5 = 20 minutes per clip (BREEZE_TIME_FACTOR in eight-gb-tts-run.sh).
bash "$TARGET" --models breeze "$@"
status=$?

echo
if [ "$status" -eq 0 ]; then
    echo "Finished. Read summary.txt in the results folder named above - or send the whole folder back."
    echo "Breeze stays on this Mac (about 3 GB). From Studio Hub 0.6.145 it runs on 8 GB Macs, so it gets fleet work here."
else
    echo "Stopped with status $status. The reason is printed above; send that back."
fi
echo
echo "You can close this window."
