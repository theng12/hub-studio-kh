#!/usr/bin/env python3
"""Stamp a kit drive's index documents **from the files actually on the drive**.

    python3 packaging/kit_stamp_index.py /Volumes/KH-STUDIO-NOVA

**Why this exists.** The drive's index — the table that answers "which build is on this drive?" —
has now been wrong three times, and every time for the same reason: two of its three version rows
were **copied by hand** from a repository file, so they described whatever that file said on the day
it was written rather than what the drive carries. Recorded in the ledger twice (`0.6.63` printed
against a `0.6.70` bundle, `0.6.70` against `0.6.89`) and again in 0.6.91. The Studio Hub row was
fixed the same way this file fixes the other two: **derive it from the artefact beside it.**

What it reads, and what it refuses to guess:

  * **Studio Hub** — the row already points at the kit's generated
    `Studio Hub KH/focused-release-manifest.json`, and is left alone for that reason. This script
    does not restate a number the manifest already derives.
  * **Story Studio** — the newest `*.dmg` in `Story Studio/`, version read from its filename.
  * **GatewayKH** — the newest `*.dmg` in `GatewayKH/installers/`, version read from its filename.

A folder with no installer, or a filename with no version in it, is left **exactly as it is** and
reported: a stamped `unknown` would be a new wrong answer rather than a fix for an old one.

It also copies the kit's own `README.md` over the drive-root copy when one exists. Those two files
were the same 130-line document at different ages — the root one dated 2026-09-26 and naming Story
**1.4.219** while the kit beside it carried 1.4.234 — which is what a second copy with no check
looks like after a few releases.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

VERSION = re.compile(r"(\d+\.\d+\.\d+)")


def newest_installer(folder: Path, pattern: str = "*.dmg") -> tuple[Path | None, str | None]:
    """The newest matching installer, and the version its own filename states."""
    try:
        files = [p for p in folder.glob(pattern) if p.is_file()]
    except OSError:
        return None, None
    if not files:
        return None, None
    newest = max(files, key=lambda p: p.stat().st_mtime)
    match = VERSION.search(newest.name)
    return newest, (match.group(1) if match else None)


def _stamp_row(text: str, label: str, version: str) -> tuple[str, bool]:
    """Set the version cell on the row whose first cell names `label`.

    Narrow on purpose: the row is matched by its own backticked folder name, and only the third
    cell of that one line is rewritten. A wider edit would be free to touch prose.
    """
    pattern = re.compile(
        r"^(\|\s*`" + re.escape(label) + r"/`\s*\|\s*[^|]+\|\s*)([^|]+)(\|)",
        re.MULTILINE,
    )
    new, count = pattern.subn(lambda m: f"{m.group(1)}{version} {m.group(3)}", text)
    return new, count == 1


def stamp(drive: Path) -> int:
    kit = drive / "Video Generation Kit"
    index = kit / "Studio Hub KH" / "README.md"
    if not index.exists():
        print(f"no kit index at {index}")
        return 1

    text = index.read_text(encoding="utf-8")
    problems: list[str] = []

    story, story_version = newest_installer(kit / "Story Studio")
    if story is None or story_version is None:
        problems.append(f"Story Studio: no installer with a version in its name under {kit / 'Story Studio'}")
    else:
        text, ok = _stamp_row(text, "Story Studio", story_version)
        if not ok:
            problems.append("Story Studio: the index has no row for that folder")
        else:
            print(f"Story Studio  -> {story_version}   ({story.name})")

    gateway, gateway_version = newest_installer(kit / "GatewayKH" / "installers")
    if gateway is None or gateway_version is None:
        problems.append(
            f"GatewayKH: no installer with a version in its name under {kit / 'GatewayKH' / 'installers'}")
    else:
        text, ok = _stamp_row(text, "GatewayKH", gateway_version)
        if not ok:
            problems.append("GatewayKH: the index has no row for that folder")
        else:
            print(f"GatewayKH     -> {gateway_version}   ({gateway.name})")

    if problems:
        # **Nothing is written when anything is unclear.** A half-stamped index is worse than the
        # stale one it replaced, because it looks current.
        for line in problems:
            print(f"REFUSED: {line}")
        return 2

    index.write_text(text, encoding="utf-8")
    print(f"stamped {index}")

    # The drive-root copy is the same document; leaving an older one there is how the two came to
    # name different versions in the first place.
    root_copy = kit / "README.md"
    if root_copy.exists():
        shutil.copyfile(index, root_copy)
        print(f"refreshed the drive-root copy: {root_copy}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(1)
    raise SystemExit(stamp(Path(sys.argv[1])))
