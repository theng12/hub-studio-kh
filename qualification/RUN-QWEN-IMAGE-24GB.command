#!/bin/sh
# Double-click this on the physical 24 GB Mac to qualify Qwen Image 2.1 MLX affine 4-bit.
set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
echo "Studio Hub — Qwen Image 2.1 MLX affine 4-bit, 24 GB physical qualification"
echo "The native runtime needs macOS 26.2 or later. Unsupported hosts are recorded as NOT MEASURED."
echo "Quit Studio Hub and finish ordinary Image work before continuing."
echo
"$HERE/qwen-image-qualification.sh" --tier 24 "$@"
status=$?
echo
echo "Finished with status $status. Results are in the dated Qwen Image 24GB Run folder beside this command."
echo "You can close this window."
exit "$status"
