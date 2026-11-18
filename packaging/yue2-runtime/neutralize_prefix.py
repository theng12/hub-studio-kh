#!/usr/bin/env python3
"""Remove the build user's home path from CPython's generated sysconfigdata.

`_sysconfigdata__darwin_darwin.py` is generated when the interpreter is built and records the
prefix it was built at — for the uv-provided Python that is
`/Users/<builder>/.local/share/uv/python/...`, which would ship in every DMG.

The file must stay (CPython's `sysconfig` imports it), and `sys.prefix` does not come from it: it is
derived from the executable's own location, so a runtime moved into the app still reports the right
prefix with these values neutralised. The script rewrites the recorded prefix to one fixed,
non-personal root and rewrites nothing else, so the same tree built anywhere has the same bytes.

    neutralize_prefix.py <runtime-tree>
"""
import re
import sys
from pathlib import Path

NEUTRAL = "/opt/studiohub-yue2-runtime"
GENERATED = "lib/python3.12/_sysconfigdata__darwin_darwin.py"
HOME_PATH = re.compile(r"/Users/[^/\"'\s]+")


def neutralize(tree: Path) -> list[str]:
    path = tree / GENERATED
    if not path.is_file():
        return []
    text = path.read_text()
    found = re.search(r'"prefix":\s*"([^"]+)"', text)
    if not found:
        raise SystemExit(f"{path}: no prefix recorded; refusing to guess what to rewrite")
    prefix = found.group(1)
    changed = sorted(set(HOME_PATH.findall(text)))
    if not changed:
        return []
    # A built tree is chmod a-w, so the write needs the directory restored and taken away again.
    directory_mode = path.parent.stat().st_mode & 0o777
    path.parent.chmod(directory_mode | 0o200)
    path.chmod(0o644)
    text_after = text.replace(prefix, NEUTRAL)
    path.write_text(text_after)
    # The interpreter is asked to recompile it if the mtime allows; a stale .pyc next to a read-only
    # source is the one way this could disagree with itself.
    cached = path.parent / "__pycache__"
    if cached.is_dir():
        for stale in cached.glob(path.stem + "*.pyc"):
            stale.chmod(0o644)
            stale.unlink()
    path.chmod(0o444)
    path.parent.chmod(directory_mode)
    return changed


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    homes = neutralize(Path(sys.argv[1]))
    print(f"neutralised {len(homes)} home path(s): {' '.join(homes) or 'none'}")
