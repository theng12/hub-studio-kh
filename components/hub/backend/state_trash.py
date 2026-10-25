"""Move a Hub state file aside instead of deleting it.

**Every removal in this project goes to `_trash`, never to `rm`.** That rule was written about the
owner's files, and it applies with more force to the Hub's own records: a roster is the only list of
which Macs were in a group, and a group the owner has just disassembled is exactly the thing he may
need to read afterwards to know which Macs he has to re-enrol.

So the two reset actions — a node leaving a fleet, and a controller disassembling its own — put the
state file they retire under `<data dir>/_trash/<reason>/<utc stamp>/`, a same-volume rename. Nothing
here parses what it moves: an archived file is a record, never an input, and a Hub that read one back
would resurrect the membership it just dropped.
"""
from __future__ import annotations

import time
from pathlib import Path

#: Where the archives go, under the data directory. `_trash` is the same name the owner's own rule
#: uses, so "look in `_trash`" is one instruction whether the file is his or the Hub's.
ROOT_NAME = "_trash"


def trash_root(data_dir: Path) -> Path:
    return Path(data_dir) / ROOT_NAME


def move_to_trash(path: Path, *, reason: str, data_dir: Path | None = None) -> str | None:
    """Move one state file into `_trash`. Returns where it went, or `None` when it was not there.

    `reason` is the subfolder — `reset-fleet`, `leave-fleet` — so two different retirements are two
    readable folders rather than one pile. The UTC stamp underneath keeps a second reset from
    overwriting what the first one kept, which matters here: the owner is doing this repeatedly
    today, and "the record of the group before last" is a thing he may want.

    Raises `OSError` if the move fails. **A caller must not carry on as though the file were
    archived**: "the old roster is kept" and "the old roster is gone" are different promises, and
    only one of them is true at a time.
    """
    path = Path(path)
    if not path.exists():
        return None
    base = Path(data_dir) if data_dir is not None else path.parent
    stamp = time.strftime("%Y-%m-%dT%H%M%SZ", time.gmtime())
    target = trash_root(base) / str(reason) / stamp
    target.mkdir(parents=True, exist_ok=True)
    moved = target / path.name
    index = 1
    while moved.exists():
        index += 1
        moved = target / f"{path.stem}.{index}{path.suffix}"
    path.replace(moved)
    return str(moved)
