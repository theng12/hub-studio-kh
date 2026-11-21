#!/usr/bin/env python3
"""Rewrite every ``*.dist-info/RECORD`` to hash the bytes the runtime actually contains.

Why this exists: `uv` writes RECORD at install time, before `normalize-shebangs` rewrites the
console scripts in ``bin/``. The recorded hashes are therefore stale, and — worse — they hash
the *pre-normalization* script, whose shebang contained the absolute build path. Two builds of
the same runtime into different directories produced different RECORD bytes and therefore
different content digests, which is exactly what a content pin cannot tolerate.

Rewriting each entry from the file on disk makes the runtime's bytes a function of the packages
and the normalizer alone, so `packaging/video-runtime/build.sh` reproduces the pinned digest from
any checkout path. The file list, sizes and hashes are unchanged in meaning; only the stale
hashes are corrected.
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path
import sys


def rewrite(site_packages: Path) -> int:
    changed = 0
    for record in sorted(site_packages.glob("*.dist-info/RECORD")):
        lines = record.read_text().splitlines()
        output: list[str] = []
        touched = False
        for line in lines:
            parts = line.split(",")
            if len(parts) != 3:
                output.append(line)
                continue
            relative, _digest, _size = parts
            target = site_packages / relative
            if target.resolve() == record.resolve():
                # A RECORD cannot hash itself; an empty self-entry is standard and is what keeps
                # two builds of the same packages byte-identical at different paths.
                rebuilt = f"{relative},,"
                output.append(rebuilt)
                touched = touched or rebuilt != line
                continue
            if not target.is_file() or target.is_symlink():
                output.append(line)
                continue
            content = target.read_bytes()
            digest = base64.urlsafe_b64encode(
                hashlib.sha256(content).digest()).rstrip(b"=").decode()
            rebuilt = f"{relative},sha256={digest},{len(content)}"
            output.append(rebuilt)
            touched = touched or rebuilt != line
        if touched:
            record.write_text("\n".join(output) + "\n")
            changed += 1
    return changed


def site_packages(root: Path) -> list[Path]:
    """Every ``lib/python3.*/site-packages`` in the tree.

    Globbed rather than the literal ``python3.11``, so a runtime on another minor version is not
    silently walked past (that would leave every RECORD hashing the *pre-rewrite* scripts).
    """
    return sorted(path for path in (root / "lib").glob("python3.*/site-packages") if path.is_dir())


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: normalize_records.py <runtime-root>", file=sys.stderr)
        return 2
    found = site_packages(Path(argv[1]))
    if not found:
        print(f"no site-packages under {argv[1]}", file=sys.stderr)
        return 2
    total = sum(rewrite(site) for site in found)
    print(f"normalized {total} RECORD file(s) in {len(found)} site-packages tree(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
