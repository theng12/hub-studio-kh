#!/usr/bin/env python3
"""Put named model packages from the kit's model-library onto THIS Mac, through the Hub's own import code.

    <the app's python3> import_kit_packages.py --app "/Applications/Hub Studio KH.app" --kit "<Studio Hub KH folder>" \
        --packages fish-audio-s2-pro-4bit,fish-audio-s2-pro-8bit,fish-audio-s2-pro-bf16

Used by RUN-16GB-FISH.command: Fish S2 Pro bf16 (and 8-bit) are shelved / 24 GB-tier, so the kit's normal copy step
("2 - Copy the models") does not put them on a 16 GB Mac. This does what the Hub's "import a model folder" does -
`model_packages.import_folder` from the app's own components: every file is checked against the manifest's SHA-256 while it
is copied into a staging folder, then published with a receipt - for exactly the packages named, and nothing else.

* It talks to the RUNNING Hub only to read (which library it uses, which packages are already verified). It never starts a Hub,
  and it never writes anywhere but the library that Hub is using.
* A package the Hub already reads as verified is left alone ("already here"). Nothing is deleted or moved.
* It stops before copying if the disk would not keep 5 GB free (the Hub's own disk governor margin).
* A Mac that is not marked keep-all moves shelved models (Fish bf16) into the Hub's Trash Folder the next time the Hub STARTS:
  the files are kept there for 15 days. Run the test straight after this, with the Hub left running.

Exit 0: every named package is on this Mac and verified. Exit 3: nothing was wrong with the Mac, but a package could not be put there
(the reason is printed). Nothing is printed that contains the Hub's token.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

READY = Path(os.environ.get("TTS8_READY_FILE") or Path.home() / "Library/Application Support/StudioHub Standalone/hub-ready.json")
MARGIN_BYTES = 5 * 10**9   # what the Hub's disk governor keeps free


def hub_get(path: str) -> dict | None:
    """One read-only call to the local Hub (token read from its own file, never printed); None when it does not answer."""
    try:
        ready = json.loads(READY.read_text())
        request = urllib.request.Request(ready["url"] + path, headers={"X-Hub-Token": ready["token"]})
        return json.loads(urllib.request.urlopen(request, timeout=30).read())
    except (OSError, ValueError, KeyError):
        return None


def library_of(rows: list[dict]) -> Path | None:
    """The library the running Hub uses: the folder of any package it reports as installed is <library>/<package>/<revision>."""
    for row in rows:
        folder = row.get("folder")
        if folder and Path(folder).is_dir() and Path(folder).parent.parent.is_dir():
            return Path(folder).parent.parent
    return None


def main(argv: list[str], hub=hub_get) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--kit", required=True, type=Path)
    parser.add_argument("--packages", required=True)
    args = parser.parse_args(argv)
    wanted = [p.strip() for p in args.packages.split(",") if p.strip()]
    resources = args.app / "Contents/Resources"
    sys.path.insert(0, str(resources / "components"))
    import model_packages   # the app's own code, so the receipt is exactly what the app checks

    listing = hub("/api/hub/model-packages")
    if not listing:
        print("STOPPED: Studio Hub is not running on this Mac, so it cannot say which library it uses.\n"
              "Open Studio Hub, wait until its window says it is ready, then run this again. Nothing was copied.")
        return 3
    rows = {row.get("package_id"): row for row in listing.get("models", [])}
    library = library_of(list(rows.values()))
    if library is None:   # nothing installed yet: the library the kit's copy step makes
        library = Path(os.environ.get("STUDIOHUB_MODELS_DIR") or Path.home() / "StudioHubModels")
    if not library.is_dir():
        print(f"STOPPED: could not find this Mac's model library (looked at {library}). Nothing was copied.")
        return 3
    catalog = model_packages.load_catalog(resources / "model-manifests")
    print(f"Model library on this Mac: {library}")
    print(f"Kit model library:         {args.kit / 'model-library'}\n")

    todo, problems = [], []
    for package_id in wanted:
        manifest = catalog.get(package_id)
        if manifest is None:
            problems.append(f"{package_id}: this Studio Hub does not know the package.")
            continue
        size = sum(int(f.get("size") or 0) for f in manifest["files"])
        if (rows.get(package_id) or {}).get("files_state") == "files_verified":
            print(f"  already here, verified: {package_id} ({size / 1e9:.1f} GB) - not copied")
            continue
        source = args.kit / "model-library" / package_id / manifest["revision"]
        if not source.is_dir():
            problems.append(f"{package_id}: not on this SSD (looked for {source}). The kit's model-library does not carry it.")
            continue
        todo.append((package_id, manifest, source, size))
    need = sum(size for *_, size in todo)
    free = shutil.disk_usage(library).free
    if todo and free < need + MARGIN_BYTES:
        problems.append(f"not enough disk space: need {need / 1e9:.1f} GB plus a 5 GB margin, this Mac has {free / 1e9:.1f} GB free.")
        todo = []
    copied = []
    for package_id, manifest, source, size in todo:
        print(f"  copying {package_id} ({size / 1e9:.1f} GB) from the kit, checking every file ...", flush=True)
        started = time.time()
        try:
            model_packages.import_folder(manifest, source, library, catalog=catalog)
        except (OSError, ValueError) as error:
            problems.append(f"{package_id}: the import refused it: {error}")
            continue
        copied.append((package_id, size, time.time() - started))
        print(f"    done in {time.time() - started:.0f} s")
    after = {r.get("package_id"): r for r in (hub("/api/hub/model-packages") or {}).get("models", [])}
    for package_id, *_ in copied:   # what the Hub itself now says about what was just copied
        row = after.get(package_id)
        if row and row.get("files_state") != "files_verified":
            problems.append(f"{package_id}: copied, but the Hub reads it as '{row.get('files_state')}' - open Studio Hub > Models and let it finish checking.")
    print()
    print("Copied from the kit to this Mac: " + (", ".join(f"{p} ({s / 1e9:.1f} GB, {t:.0f} s)" for p, s, t in copied) if copied else "nothing (all named packages were already here)"))
    for line in problems:
        print("PROBLEM: " + line)
    return 3 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
