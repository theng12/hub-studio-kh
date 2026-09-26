#!/bin/sh
# Double-click this to measure LTX-2.3 video generation on this Mac.
#
# Why a .command rather than the .sh: Finder has no application bound to a `.sh`, so
# double-clicking `sixteen-gb-ltx-video-run.sh` opens it in TextEdit and looks broken. The owner
# hit exactly this on his 8 GB Mac with the chat runner and again with the Z-Image one, so this
# is a wrapper for the same reason those two are: one copy of the work, and a name Finder opens.
#
# What it measures, and why it matters: the only published memory figure for LTX-2.3 on Apple
# Silicon is the packager's "at least 24 GB unified RAM for safe T2V inference" — a vendor claim
# measured on an M4 Max with 64 GB. The fleet is 15 sixteen-GB machines out of ~53, so whether
# this runs at 16 GB decides whether video is a fleet feature or a two-machine curiosity.
#
# It runs the SCENE-FIRST path (image-to-video), because that is the requirement: the owner
# generates a scene image first and animates it. `--require-image-to-video` makes a text-to-video
# fallback impossible — a run that quietly became the wrong operation would be a measurement of
# the wrong thing wearing the right label.
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TARGET="$HERE/sixteen-gb-ltx-video-run.sh"

if [ ! -f "$TARGET" ]; then
    echo "sixteen-gb-ltx-video-run.sh is not beside this file."
    echo
    echo "Keep RUN-16GB-LTX-VIDEO.command and sixteen-gb-ltx-video-run.sh in the same folder."
    echo "Looked in: $HERE"
    echo
    read -r _ || true
    exit 1
fi

echo "Studio Hub — LTX-2.3 video measurement (16 GB tier)"
echo "=================================================="
echo
echo "This runs ONE video generation and writes down what it actually cost: the machine, the"
echo "runtime versions, wall-clock time, peak MLX Metal allocation, and peak process footprint."
echo
echo "It needs two things on this Mac, and it will tell you plainly if either is missing:"
echo "  1. the LTX runtime (a Python 3.11 environment — the Image runtime is 3.12 and cannot host it)"
echo "  2. the model  LTX-2.3 22B RAM-12GB  (mixed 2-8 bit, 17.81 GiB)"
echo
echo "Nothing is installed, moved or deleted. Results land in a new folder beside this file."
echo
printf "Press Return to start, or close this window to stop: "
read -r _ || true
echo

sh "$TARGET"
STATUS=$?

echo
echo "=================================================="
case "$STATUS" in
    0) echo "Finished. Read measurement.json for the numbers." ;;
    3) echo "Refused before measuring — the reason is in the output above, and in measurement.json." ;;
    4) echo "The generation itself did not finish. run.log holds the error; nothing was deleted." ;;
    *) echo "Stopped with status $STATUS. run.log holds the detail." ;;
esac
echo
echo "You can close this window."
