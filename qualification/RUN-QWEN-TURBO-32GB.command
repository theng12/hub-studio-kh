#!/bin/sh
# Physical-tier qualification only; never runs alongside ordinary model work.
set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
echo "Studio Hub — Qwen Image 2.1 Turbo Low-RAM MLX 4-bit, 32 GB qualification"
echo "Wait for the shared handoff's current Hub-work/song-run gates, then finish all model work first."
echo "8 GB is unsupported. No memory or speed claim exists until this run passes."
"$HERE/qwen-image-qualification.sh" --tier 32 --turbo "$@"
status=$?
echo "Finished with status $status. The runner prints its evidence folder."
exit "$status"
