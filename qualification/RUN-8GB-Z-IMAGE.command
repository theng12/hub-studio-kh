#!/bin/sh
# Double-click this to run the 8 GB Z-Image-Turbo measurement (SH-7).
#
# Why this file exists: exactly the same reason `RUN-CHAT-TIER.command` does. Finder has no
# application bound to a `.sh`, so double-clicking `eight-gb-z-image-run.sh` opens it in TextEdit
# and looks broken — which is what the owner hit on his 8 GB Mac, in the same session that he hit it
# with the chat tier runner. The fix was made for chat and not for this one; that was an omission.
#
# It is a wrapper rather than a rename so the real script keeps its own name in its own
# instructions, and so there is one copy of the measurement to keep correct.
#
# Before it runs anything it checks the one thing that stops this measurement dead: the Image
# runtime inside the installed app. Z-Image-Turbo 6B MLX 4-bit was qualified on **mflux 0.20.0**,
# and releases 0.6.63-0.6.67 shipped 0.17.5 by mistake, so an out-of-date app refuses. This says so
# in plain words before the refusal, rather than after it.
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TARGET="$HERE/eight-gb-z-image-run.sh"
APP="${STUDIOHUB_APP:-/Applications/Hub Studio KH.app}"

if [ ! -f "$TARGET" ]; then
    echo "eight-gb-z-image-run.sh is not beside this file."
    echo
    echo "Keep RUN-8GB-Z-IMAGE.command and eight-gb-z-image-run.sh in the same folder — the kit's"
    echo "Technical folder has both."
    echo
    echo "Looked in: $HERE"
    exit 1
fi

# Which mflux the installed app carries. This is the check the release loop did not have.
SHIPPED=""
for pkg in "$APP"/Contents/Resources/runtimes/image/lib/python*/site-packages; do
    [ -d "$pkg" ] || continue
    for d in "$pkg"/mflux-*.dist-info; do
        [ -d "$d" ] || continue
        SHIPPED=$(basename "$d" .dist-info)
        SHIPPED=${SHIPPED#mflux-}
    done
done

echo "Studio Hub — 8 GB Z-Image-Turbo run"
echo
echo "This measures Z-Image-Turbo 6B MLX 4-bit on an 8 GB Mac, which is the one tier nobody has"
echo "watched it on. It takes several minutes, writes a results folder beside itself, and changes"
echo "nothing on this Mac: no app is installed, no model is moved."
echo
if [ -n "$SHIPPED" ]; then
    echo "Image runtime in this app: mflux $SHIPPED"
    case "$SHIPPED" in
        0.20*) echo "That is the engine this model was qualified on. Good." ;;
        *) echo "That is NOT the engine this model was qualified on (needs 0.20.x). The run will"
           echo "refuse rather than record numbers from a different engine. Install a kit at 0.6.68"
           echo "or later, which carries mflux 0.20.0, and run this again." ;;
    esac
else
    echo "No Studio Hub app found at: $APP"
    echo "Install one from the kit (double-click '1 - Install the app.command' at the kit's root),"
    echo "or set STUDIOHUB_APP to the app you want measured."
fi
echo

cd "$HERE" || exit 1
bash "$TARGET"
status=$?

echo
if [ "$status" -eq 0 ]; then
    echo "Finished. The block above is the result — send it back as-is."
else
    echo "Stopped with status $status. The reason is printed above; send that back."
fi
echo
echo "You can close this window."
