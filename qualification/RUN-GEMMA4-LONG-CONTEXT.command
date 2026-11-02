#!/bin/sh
set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
echo "Studio Hub — Gemma 4 26B-A4B QAT MLX 4-bit Long-Context Probe"
echo "Long-context probe (48K and 64K input tokens, ~4K answer) on this physical Mac only."
echo "Stop rule: stops before next size if swap grows or peak footprint exceeds 26 GB."
echo "This never enables the model or alters Studio Hub settings."
echo
"$HERE/moe-qualification.sh" gemma4-26b-a4b-qat-4bit --suite long-context "$@"
status=$?
echo
echo "Finished with status $status. Review SUMMARY.txt under Desktop/StudioHub LLM Qualification."
echo "You can close this window."
exit "$status"
