"""Who owns a model library: only the Hub that owns one may move packages out of it.

Why this exists (data loss, 2026-10-01/02): a Hub started with a throwaway ``--data-dir`` but the
REAL model library shelved and retired the real weights into the throwaway directory's Trash Folder,
and deleting that directory destroyed them. Shelving, retiring and every other move out of a library
are therefore allowed only to the Hub that owns it.

The owner is recorded in ``<library>/.studiohub/owner.json`` (the data directory and the machine):

* an **unclaimed** library (no record: every library made before this check) is claimed by the first
  Hub whose data directory is not under a temp folder;
* a library is **owned** by the Hub whose data directory is the recorded one, on the recorded machine;
* anything else - a different data directory, another machine, a damaged record, a data directory in a
  temp folder (``/tmp``, ``/var/folders``, ``$TMPDIR``) - treats the library as READ-ONLY: nothing is
  shelved, retired, trashed or pruned, and the reason is logged. The one exception is a record whose
  data directory no longer exists on this same machine (the app was reinstalled elsewhere): a real
  (non-temp) data directory takes it over.

Nothing here deletes anything.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

OWNER_FILE = Path(".studiohub") / "owner.json"
SCHEMA = 1

_TEMP_FOLDERS = ("/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp", "/var/folders", "/private/var/folders")
_MACHINE: str | None = None


@dataclass(frozen=True)
class Ownership:
    owned: bool
    reason: str = ""
    claimed_now: bool = False


def temp_roots() -> list[Path]:
    """Every folder that is a temp folder on this Mac, as resolved paths."""
    names = [*_TEMP_FOLDERS, tempfile.gettempdir(), os.environ.get("TMPDIR", "")]
    roots: list[Path] = []
    for name in names:
        if name:
            try:
                roots.append(Path(name).resolve())
            except OSError:
                continue
    return roots


def under_temp(path: str | os.PathLike[str]) -> bool:
    """True when `path` is a temp folder or inside one (symlinks resolved)."""
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return True                         # cannot tell: treat as unsafe
    return any(resolved == root or root in resolved.parents for root in temp_roots())


def moving_into_temp(library: str | os.PathLike[str], trash_root: str | os.PathLike[str]) -> bool:
    """True when weights would move from a real library into a temp folder (never allowed)."""
    return under_temp(trash_root) and not under_temp(library)


def machine_id() -> str:
    """This Mac's identity (hardware UUID, else host name), so a library on a drive is never 'owned'
    by whichever Mac it is plugged into."""
    global _MACHINE
    if _MACHINE is None:
        found = ""
        try:
            out = subprocess.run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                 capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    found = line.split("=", 1)[1].strip().strip('"')
                    break
        except (OSError, subprocess.SubprocessError):
            pass
        if not found:
            import platform
            found = platform.node()
        _MACHINE = found
    return _MACHINE


def _record(library: Path) -> tuple[dict | None, str]:
    """(record, problem). No file is (None, ''); a damaged one is (None, why)."""
    path = library / OWNER_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, ""
    except OSError as exc:
        return None, f"the ownership record could not be read ({exc})"
    try:
        value = json.loads(text)
    except ValueError:
        return None, "the ownership record is not valid JSON"
    if (not isinstance(value, dict) or not isinstance(value.get("data_dir"), str)
            or not isinstance(value.get("machine"), str)):
        return None, "the ownership record is not in a shape this build reads"
    return value, ""


def _write(library: Path, data_dir: Path) -> bool:
    path = library / OWNER_FILE
    partial = path.with_name(f".owner-{os.getpid()}.tmp")
    try:
        if path.parent.is_symlink():
            return False
        path.parent.mkdir(mode=0o700, exist_ok=True)
        partial.write_text(json.dumps({
            "schema": SCHEMA, "data_dir": str(data_dir), "machine": machine_id(),
            "claimed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, indent=2) + "\n", encoding="utf-8")
        os.chmod(partial, 0o600)
        os.replace(partial, path)
        return True
    except OSError:
        try:
            partial.unlink()
        except OSError:
            pass
        return False


def resolve(library: str | os.PathLike[str], data_dir: str | os.PathLike[str]) -> Ownership:
    """May the Hub with this data directory move packages out of this library? Claims an unclaimed one."""
    lib = Path(library).expanduser().resolve()
    data = Path(data_dir).expanduser().resolve()
    if under_temp(data):
        return Ownership(False, f"this Hub's data directory ({data}) is in a temp folder, which never owns a library")
    record, problem = _record(lib)
    if problem:
        return Ownership(False, problem)
    if record is None:
        if _write(lib, data):
            return Ownership(True, "claimed an unclaimed library", claimed_now=True)
        return Ownership(False, "the library is not writable, so ownership cannot be recorded")
    same_machine = record["machine"] == machine_id()
    if same_machine and Path(record["data_dir"]).resolve() == data:
        return Ownership(True)
    if same_machine and not Path(record["data_dir"]).exists() and _write(lib, data):
        return Ownership(True, f"took over from {record['data_dir']}, which no longer exists", claimed_now=True)
    owner = record["data_dir"] if same_machine else f"{record['data_dir']} on another Mac"
    return Ownership(False, f"the library belongs to the Hub at {owner}")


def refusal(library: str | os.PathLike[str], data_dir: str | os.PathLike[str], what: str) -> str | None:
    """None when allowed; otherwise the sentence to log for why `what` (e.g. 'shelving') was skipped."""
    state = resolve(library, data_dir)
    if state.owned:
        return None
    return (f"not {what}: {state.reason}. This Hub treats {library} as read-only "
            f"(nothing is shelved, retired or moved).")
