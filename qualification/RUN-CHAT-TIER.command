#!/bin/sh
# Double-click this to run the chat tier measurement (SH-6).
#
# Why this file exists: Finder has no application bound to a `.sh`, so double-clicking
# `chat-tier-run.sh` opens it in TextEdit and looks broken. A `.command` is handed to Terminal
# instead, which is what the drive's other helpers (`install_app.command`,
# `verify-this-app.command`, `OPEN-THE-LOG.command`) already rely on.
#
# It is a wrapper rather than a rename so the real script keeps its own name in its own
# instructions, and so there is exactly one copy of the measurement to keep correct.
#
# Run this on a 16 GB Mac first, then on one 8 GB Mac. The script refuses any other tier rather
# than guessing, and it says which check failed when it does.
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TARGET="$HERE/chat-tier-run.sh"

if [ ! -f "$TARGET" ]; then
    echo "chat-tier-run.sh is not beside this file."
    echo
    echo "Keep RUN-CHAT-TIER.command and chat-tier-run.sh in the same folder — the kit's"
    echo "Technical folder has both. If you copied only this file to the Desktop, copy"
    echo "chat-tier-run.sh over as well, or run it straight off the drive."
    echo
    echo "Looked in: $HERE"
    exit 1
fi

echo "Studio Hub — chat tier run"
echo
echo "Takes a few minutes, prints one block to send back, and writes the same thing to"
echo "~/Desktop/StudioHub Chat Tier Run/. Close other apps first, and leave the Mac alone"
echo "while it runs; on an 8 GB Mac this is genuinely tight, which is the point."
echo

cd "$HERE" || exit 1
bash "$TARGET"
status=$?

echo
if [ "$status" -eq 0 ]; then
    echo "Finished. The block above is the result — send it back as-is."
    echo "It is also saved in ~/Desktop/StudioHub Chat Tier Run/ (VERDICT.txt)."
else
    echo "Stopped with status $status. The reason is printed above; send that line back."
fi
echo
echo "You can close this window."
