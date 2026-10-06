#!/bin/sh
# Double-click this on the physical 16 GB Mac to qualify Qwen Image 2.1 MLX affine Q4 via mflux.
set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
echo "Studio Hub — Qwen Image 2.1 MLX affine Q4, 16 GB physical qualification"
echo "This is the bounded mflux package. The 8 GB tier remains NOT MEASURED."
echo "Quit Studio Hub and finish ordinary Image work before continuing."
echo
"$HERE/qwen-image-qualification.sh" --tier 16 "$@"
status=$?
echo
echo "Finished with status $status. Results are in the dated Qwen Image 16GB Run folder beside this command."
echo "You can close this window."
exit "$status"
