#!/bin/sh
# Portable bridge from a Finder .command to Studio Hub's bundled Voice Python.
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
KIT=$(CDPATH= cd -- "$HERE/.." && pwd)
MODEL="${1:-}"
if [ -z "$MODEL" ]; then
    echo "No qualification model was selected." >&2
    exit 4
fi
shift

PYTHON=""
for app in "/Applications/Hub Studio KH.app" "$HOME/Applications/Hub Studio KH.app" "$KIT/Hub Studio KH.app"; do
    candidate="$app/Contents/Resources/runtimes/voice/bin/python3"
    if [ -x "$candidate" ]; then
        PYTHON="$candidate"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "CHECK FAILED: no Studio Hub Voice runtime was found in /Applications, $HOME/Applications, or this kit." >&2
    echo "Studio Hub was not started and no system Python or download was used." >&2
    exit 3
fi

PYTHONDONTWRITEBYTECODE=1 exec "$PYTHON" -B "$HERE/moe_qualification.py" \
    --model "$MODEL" --kit-root "$KIT" --technical-root "$HERE" \
    --runtime-python "$PYTHON" "$@"
