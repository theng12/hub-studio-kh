#!/bin/bash
# Image generation on an 8 GB Mac: Z-Image-Turbo 4-bit (thumbnails) and FLUX.2 Klein 4B 4-bit (scenes), through the Studio Hub
# that is installed and running on THIS Mac - the same measurement as eight-gb-tts-run.sh, for images.
#
#   bash eight-gb-image-run.sh                     the test: 3 images per model at 1280x720 (Z-Image-Turbo 9 steps, FLUX.2 Klein 4 steps), guard bypassed
#   bash eight-gb-image-run.sh --quick             a short check (1 image per model, 1024x576, 2 steps) - the script's own self-test, not 8 GB evidence
#   bash eight-gb-image-run.sh --models zimage     only these (zimage, klein)
#   bash eight-gb-image-run.sh --images 5          images per model (default 3)
#   bash eight-gb-image-run.sh --allow-non-target  run the full test on a Mac that is not 8 GB (the summary says it is not 8 GB evidence)
#
# (Easier: double-click RUN-8GB-IMAGE.command, which runs this.) Knobs and what it measures: README-8gb-image.md and the top of
# eight_gb_image_run.py, which holds the program; ssd_wear.py (SSD writes) must sit beside it. This file only finds the Python that goes with
# the installed app (the machine may have no other, and the system /usr/bin/python3 can pop up a developer-tools installer) and runs it.
set -u
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
SELF="${BASH_SOURCE[0]}"
HERE=$(CDPATH= cd -- "$(dirname -- "$SELF")" && pwd)

APP=""; PY=""
for candidate in "${STUDIOHUB_APP:-}" "/Applications/Hub Studio KH.app" "$HOME/Applications/Hub Studio KH.app"; do
  [ -n "$candidate" ] || continue
  for rt in image voice; do
    [ -x "$candidate/Contents/Resources/runtimes/$rt/bin/python3" ] && { APP="$candidate"; PY="$candidate/Contents/Resources/runtimes/$rt/bin/python3"; break 2; }
  done
done
if [ -z "$APP" ]; then
  cat >&2 <<MSG
STOPPED: Studio Hub is not installed on this Mac, so there is nothing to test.

  Install it from the kit: double-click "1 - Install the app" (or "Update this Mac") at the kit's
  root, open Studio Hub once, then run this again. If it is installed somewhere unusual, set
  STUDIOHUB_APP to its path.

Nothing was run and nothing was written.
MSG
  exit 3
fi
for f in eight_gb_image_run.py ssd_wear.py; do
  [ -f "$HERE/$f" ] || { echo "STOPPED: $f is not beside this script. Keep it in the same folder (the kit's Technical folder has it). Looked in: $HERE" >&2; exit 3; }
done
exec "$PY" "$HERE/eight_gb_image_run.py" "$HERE" "$APP" "$@"
