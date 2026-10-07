#!/bin/sh
set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
echo "Studio Hub — Qwen3.6 35B-A3B MLX 4-bit"
echo "Full test suite on this physical Mac only. This never enables the model."
echo
"$HERE/moe-qualification.sh" qwen36-35b-4bit --suite full "$@"
status=$?
echo
echo "Finished with status $status. Review SUMMARY.txt under Desktop/StudioHub LLM Qualification."
echo "You can close this window."
exit "$status"
