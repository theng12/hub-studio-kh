#!/bin/sh
# Studio Hub KH — is this copy of the kit complete, and does its app start?
#
# The kit is ~39 GB, most of it the model library. A copy that stopped early, or that was dragged
# without finishing, looks exactly like a good one in Finder: the folder is there, the app opens,
# and the failure only appears later as a model that reads as "not installed" on a Mac you have
# already driven to. So this checks the copy itself, then starts the app that is *in this kit*
# (not one installed on the Mac) and asks it for its health.
#
#   sh "/Volumes/<drive>/Studio Hub App/Technical/verify-kit.sh"
#
# What it can and cannot see: the library records each model file's size, not its hash, so this
# catches a copy that stopped early or was interrupted - the failure that looks fine in Finder and
# only shows up later as a model reading "not installed" on a Mac you have already driven to. It
# cannot detect silent bit-rot on an old drive, and it does not pretend to.
#
# Read-only: it never touches your models, your preferences or your job history, and it is safe to
# run while Studio Hub is running.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
KIT=$(dirname "$HERE")
APP="$KIT/Studio Hub.app"
LIB="$KIT/model-library"
PY="$APP/Contents/Resources/runtimes/voice/bin/python3"

if [ ! -d "$APP" ]; then
  echo "FAIL — no app at $APP"
  echo "     Copy the whole 'Studio Hub App' folder, not just its contents."
  exit 1
fi
if [ ! -x "$PY" ]; then
  echo "FAIL — the app in this kit has no bundled runtime at"
  echo "       $PY"
  echo "     That copy is incomplete. Delete it and copy the folder again."
  exit 1
fi
if [ ! -d "$LIB" ]; then
  echo "FAIL — no model library at $LIB"
  exit 1
fi

"$PY" - "$KIT" "$LIB" "$APP" <<'PYTHON'
import json
import sys
from pathlib import Path

kit, library, app = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
problems: list[str] = []

def total_bytes(root: Path) -> tuple[int, int]:
    """Files and their sizes, following symlinks - the same rule the kit manifest records with.

    Counting link targets again is deliberate: the bundle is full of them (`python3 ->
    python3.12`), and excluding them made this check disagree with the manifest by 75 MB.
    """
    count = size = 0
    for path in root.rglob("*"):
        if path.is_file():
            count += 1
            size += path.stat().st_size
    return count, size

# 1. The app is the build this kit says it is, and all of it is here.
try:
    manifest = json.loads((kit / "focused-release-manifest.json").read_text())
except (OSError, ValueError) as exc:
    print(f"FAIL — cannot read the kit manifest: {exc}")
    raise SystemExit(1)
recorded_bytes = manifest.get("app", {}).get("bytes")
count, size = total_bytes(app)
if isinstance(recorded_bytes, int) and size != recorded_bytes:
    problems.append(f"the app is {size:,} bytes; the kit records {recorded_bytes:,}")
print(f"  app      {manifest.get('version', '?')}  {count:,} files  {size / 1e9:.2f} GB")

# 2. Every model the kit records is installed, with the file sizes the library recorded for it.
try:
    installed = json.loads((library / ".studiohub" / "installed.json").read_text())
except (OSError, ValueError) as exc:
    problems.append(f"the library has no readable install record: {exc}")
    installed = {}
expected_bytes = 0
checked = missing = wrong = 0
for package_id, record in installed.items():
    revision = record.get("revision") or ""
    for relative, entry in (record.get("files") or {}).items():
        expected_bytes += int(entry.get("st_size") or 0)
        candidates = [library / package_id / revision / relative, library / package_id / relative]
        found = next((path for path in candidates if path.is_file()), None)
        if found is None:
            missing += 1
            if len(problems) < 6:
                problems.append(f"missing: {package_id}/{relative}")
            continue
        if int(entry.get("st_size") or -1) != found.stat().st_size:
            wrong += 1
            if len(problems) < 6:
                problems.append(f"wrong size: {package_id}/{relative}")
            continue
        checked += 1
lib_count, lib_size = total_bytes(library)
advertised = len(manifest.get("models") or [])
print(f"  library  {len(installed)} installed package(s), {advertised} advertised  "
      f"{lib_count:,} files on disk  {lib_size / 1e9:.1f} GB")
print(f"  checked  every one of the {checked:,} model file(s) the library records "
      f"({expected_bytes / 1e9:.1f} GB), by size")
if advertised and len(installed) < advertised:
    # A kit may deliberately carry a subset of the catalogue: the app advertises every
    # model it supports and offers the rest as downloads. That is not a broken copy, so
    # say it plainly rather than failing the whole drive.
    print(f"  note     {advertised - len(installed)} advertised model(s) are not in this "
          f"kit; the app offers them as downloads")

if problems:
    print("\nFAIL — this copy is not complete:")
    for problem in problems:
        print(f"  - {problem}")
    print("\nDelete the folder and copy it again; do not hand this drive to a Mac.")
    raise SystemExit(1)
print("\nThe copy is complete.")
PYTHON

echo
echo "--- does the app in this kit start? ---"
APP="$APP" sh "$HERE/verify-launch.sh"
