"""The removable-volume check, done **once per process**, never inside a request, and **only
inside a drive that is named like a kit**.

**Why it exists, part one - the freeze (0.6.206 to 0.6.209).** 0.6.206 added the first walk of
``/Volumes`` so the install screen could say which SSD kit is mounted. It ran inside
``GET /api/hub/model-presets``, and the app read that route from ``refresh()`` on every connect
and every poll. macOS asks for permission to read removable volumes, and an **ad-hoc signed**
build loses that permission on every update - so ``open()`` sat waiting on an unanswered prompt,
holding a threadpool slot. Enough polls filled the pool, and ``/api/health`` and
``/api/hub/resources`` stopped answering while the Mac looked frozen. Clicking **Allow** on the
prompt revived it, which is how the owner found the cause at the Macs. 0.6.210 moved the walk
into a bounded child process; that fixed the freeze and not the prompt.

**Why it exists, part two - the prompt itself (owner, 2026-10-30).** The child still opened
**every** mounted volume at every Hub start, so a Mac with no kit attached was asked about
whatever it did have: any USB drive ("access files on a removable volume") and any mounted
network share ("access files on a network volume"). An ad-hoc signed build is asked again after
every update, so the owner clicked Allow on Mac after Mac. This module now decides **which**
volumes to open from the kernel's mount table alone - the mount point's name and filesystem -
and opens nothing else. A Mac with no drive named like a kit reads no volume at all, so macOS has
nothing to ask about.

**The rule that follows, and this whole module is it:** the check runs in a **child process**
with a hard timeout, at most **once per process**, and every request reads only the cached
answer. A child that cannot get permission is killed at the timeout; the Hub keeps serving and
reports an honest ``needs_permission`` state instead of a hung slot.

**The second rule: the name is the only thing about a drive the Hub may learn without reading
it.** A read of a volume's root is exactly what macOS asks about, so there is no "look inside to
see whether it is a kit" step before the permission exists. A kit drive is named ``KH-STUDIO-*``
(`packaging/prepare-kit.sh KH-STUDIO-XXXX`, and every drive the kit has been written to), and a
drive with any other name is not looked into - so it is not found either. That is the documented
limit, not an oversight: renaming a kit drive asks for that name, and a renamed drive is found
again by renaming it back (or by choosing the library by hand on the Models page).

**Honest states, because a zero that means two things is a defect:**

* ``ready`` with kits - the drives that were read hold them.
* ``needs_permission`` - a drive named like a kit exists and macOS would not let the Hub read it,
  or the child died, timed out or answered unreadably. Never an empty kit list, which would read
  as "no kit is mounted".
* ``ready`` with no kits and a reason that says **which** answer this is: no kit-named drive is
  mounted (nothing was opened), a kit-named drive is mounted but holds no ``model-library``, or
  the only kit-named mount is a network share (never read). ``looked_inside`` is published beside
  it, so "I did not look in every volume" is visible in the answer and not only in the words.

The probe is started at Hub start-up (`standalone.create_app`'s lifespan). Nothing on a request
path may call `probe()`; `status()` and `kits()` never block and never touch a volume.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

#: Where removable volumes mount, and the folder a kit drive carries its models under. Kept here
#: because this is the one place that looks at the folder.
VOLUME_ROOT = Path("/Volumes")
KIT_FOLDER = "model-library"

#: How a kit drive's name begins. Every drive the kit is written to is named this way - the
#: parameter is `KH-STUDIO-XXXX` in `packaging/prepare-kit.sh` - and the name is the only thing
#: this module learns about a drive before it is allowed to read it. A wider list is one edit
#: here; the drive still has to *be* a kit for anything to be found inside it.
KIT_VOLUME_PREFIXES = ("KH-STUDIO-",)

#: Filesystems this module never opens, even when the mount is named like a kit. macOS gates
#: these behind its own **Network Volumes** permission, and a kit drive is a local disk, so a
#: mount here is not a kit this Hub can copy from without the one permission it is avoiding.
NETWORK_FILESYSTEMS = ("afpfs", "autofs", "cifs", "devfs", "nfs", "smbfs", "webdav")

#: How long the child may take before it is killed. The owner's cause is an **unanswered** macOS
#: permission prompt, which never returns on its own; two seconds is long enough for a real walk
#: and short enough that nobody notices it.
PROBE_TIMEOUT_SECONDS = 2.0

#: The child prints one line beginning with this, so a warning printed by an interpreter or an
#: import cannot be mistaken for the answer.
_PROBE_PREFIX = "studiohub-volume-probe:"

#: The child's whole job: read the mount table, keep the mounts named like a kit, look inside
#: those only, and answer in JSON. It is a separate process on purpose - a blocked read must be
#: something the parent can kill, not a thread this Hub then waits on for ever.
#:
#: Arguments: the volume root, the kit folder, comma-separated name prefixes, comma-separated
#: filesystems never to read, and an optional file to read the mount table from (tests only; the
#: production child runs `/sbin/mount`, which reads the kernel's list and touches no volume).
_CHILD_SOURCE = r'''
import json
import os
import re
import stat
import subprocess
import sys

root, folder, prefixes, network, mounts_from = (
    sys.argv[1],
    sys.argv[2],
    [item for item in sys.argv[3].split(",") if item],
    set(item for item in sys.argv[4].split(",") if item),
    sys.argv[5] if len(sys.argv) > 5 else "",
)
MOUNT_LINE = re.compile(r"^.+ on (/\S+) \(([^)]*)\)$")


def answer(payload):
    print("__PREFIX__" + json.dumps(payload))
    raise SystemExit(0)


def mount_table():
    """The mounted filesystems, from the kernel. Reading it opens no volume."""
    if mounts_from:
        with open(mounts_from, "r", encoding="utf-8") as handle:
            return handle.read().splitlines()
    done = subprocess.run(["/sbin/mount"], capture_output=True, text=True)
    if done.returncode != 0:
        raise OSError(done.stderr.strip() or "/sbin/mount exited %d" % done.returncode)
    return done.stdout.splitlines()


try:
    lines = mount_table()
except Exception as exc:  # noqa: BLE001 - every failure means "I could not look"
    answer({"ok": False, "reason": "the mounted disks could not be listed: %s" % (exc,)})

root_text = root.rstrip("/") or "/"
mounts, seen = [], set()
for line in lines:
    match = MOUNT_LINE.match(line.strip())
    if not match:
        continue
    point, options = match.group(1), match.group(2)
    parent, _, name = point.rpartition("/")
    if parent != root_text or not name or name in seen:
        continue
    seen.add(name)
    mounts.append((point, options.split(",")[0].strip()))

kits, looked_inside, unreadable, not_read = [], [], [], []


def could_not_read(path, exc):
    unreadable.append({"path": path, "reason": str(exc)})


def is_directory(path):
    """True or False; raises OSError when the answer could not be had.

    **Not `Path.is_dir()`**: it answers False for a refused read, which would turn "macOS would
    not let me look" into "there is no kit there" - the one confusion this module exists to stop.
    """
    try:
        return stat.S_ISDIR(os.stat(path).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False


def names(path):
    """The visible entries of `path`, sorted.

    Names beginning with a dot are left out, exactly as the old `glob("*")` left them out: macOS
    keeps its own metadata folders on every volume (`.DocumentRevisions-V100`, `.Spotlight-V100`,
    `.TemporaryItems`, `.Trashes`), they are protected, and none of them can hold a kit library.
    Measured on this Mac 2026-10-30: reading them returns **EPERM**, which would otherwise be
    recorded as a drive the Hub could not read.
    """
    with os.scandir(path) as scanned:
        return sorted(entry.name for entry in scanned if not entry.name.startswith("."))


for point, kind in mounts:
    name = point.rsplit("/", 1)[1]
    if not any(name.startswith(prefix) for prefix in prefixes):
        # Not named like a kit: **never opened**. On a Mac with no kit drive this is every
        # mount, so no volume is read and macOS has nothing to ask about.
        continue
    if kind in network:
        # Named like a kit and mounted over the network: the other permission this module
        # exists to avoid, and a kit is a local disk. Recorded, never opened.
        not_read.append({"path": point, "reason": "a network volume is never read"})
        continue
    looked_inside.append(point)
    try:
        firsts = names(point)
    except OSError as exc:
        could_not_read(point, exc)
        continue
    candidates = [point + "/" + folder]
    candidates.extend(point + "/" + first + "/" + folder for first in firsts)
    for first in firsts:
        try:
            seconds = names(point + "/" + first)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as exc:
            could_not_read(point + "/" + first, exc)
            continue
        candidates.extend(point + "/" + first + "/" + second + "/" + folder
                          for second in seconds)
    for candidate in candidates:
        try:
            found = is_directory(candidate)
        except OSError as exc:
            could_not_read(candidate, exc)
            continue
        if found and candidate not in kits:
            kits.append(candidate)

answer({"ok": True, "kits": kits, "looked_inside": looked_inside,
        "unreadable": unreadable, "not_read": not_read})
'''.replace("__PREFIX__", _PROBE_PREFIX)

_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_STATE: dict | None = None
#: Only tests replace this; production always runs the child above.
_PROBE_COMMAND: list[str] | None = None


def _child_command(root: Path | str = VOLUME_ROOT, folder: str = KIT_FOLDER,
                   prefixes: tuple[str, ...] = KIT_VOLUME_PREFIXES,
                   network: tuple[str, ...] = NETWORK_FILESYSTEMS,
                   mounts_from: Path | str | None = None) -> list[str]:
    """The child that does the only volume read in this process tree.

    ``mounts_from`` is the test seam: a file of `/sbin/mount` lines to use instead of asking the
    kernel. Production never passes it, so the shipped child always reads the real mount table.
    """
    command = [sys.executable, "-I", "-c", _CHILD_SOURCE, str(root), folder,
               ",".join(prefixes), ",".join(network)]
    if mounts_from:
        command.append(str(mounts_from))
    return command


def _command() -> list[str]:
    if _PROBE_COMMAND is not None:
        return list(_PROBE_COMMAND)
    return _child_command()


def _answer(state: str, *, reason: str | None, kits: list[str] | None = None,
            looked_inside: list[str] | None = None, unreadable: list[dict] | None = None,
            not_read: list[dict] | None = None) -> dict:
    return {"state": state, "path": str(VOLUME_ROOT), "reason": reason,
            "kits": list(kits or []), "looked_inside": list(looked_inside or []),
            "unreadable": [dict(item) for item in unreadable or []],
            "not_read": [dict(item) for item in not_read or []],
            "checked_at": time.time()}


def _drive_names(paths) -> list[str]:
    """The drive name of each path, in the order given - `/Volumes/X/...` -> `X`."""
    names: list[str] = []
    for path in paths:
        try:
            name = Path(str(path)).relative_to(VOLUME_ROOT).parts[0]
        except (ValueError, IndexError):
            name = Path(str(path)).name or str(path)
        if name not in names:
            names.append(name)
    return names


def _listed(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _decide(*, kits: list[str], looked_inside: list[str], unreadable: list[dict],
            not_read: list[dict]) -> dict:
    """Turn the child's facts into the one honest answer.

    ``needs_permission`` is kept for the case where the Hub *could not tell*: a kit-named drive
    it was not allowed to read. A kit-named drive that was read and holds nothing is a different
    fact and says so, and "no kit-named drive is mounted" never borrows the word "checked".
    """
    if unreadable:
        names = _listed(_drive_names(item.get("path") for item in unreadable))
        if not kits:
            return _answer("needs_permission", reason=(
                f"The check for removable volumes needs permission: macOS would not let the Hub "
                f"read {names}. Click Allow on the macOS prompt for Removable Volumes, then "
                f"restart this Mac's Hub services so it can look again."),
                looked_inside=looked_inside, unreadable=unreadable, not_read=not_read)
        # A kit was found and something on a kit-named drive was not read: name the exact path,
        # rather than letting it read as absent (or claiming the whole drive was unreadable).
        return _answer("ready", reason=(
            f"A kit was found, but {unreadable[0].get('path')} could not be read, so a kit there "
            f"was not found: {unreadable[0].get('reason')}"), kits=kits,
            looked_inside=looked_inside, unreadable=unreadable, not_read=not_read)
    if kits:
        return _answer("ready", reason=None, kits=kits, looked_inside=looked_inside,
                       not_read=not_read)
    if looked_inside:
        names = _drive_names(looked_inside)
        return _answer("ready", reason=(
            f"{_listed(names)} {'is' if len(names) == 1 else 'are'} mounted but "
            f"{'holds' if len(names) == 1 else 'hold'} no {KIT_FOLDER} folder."),
            looked_inside=looked_inside, not_read=not_read)
    if not_read:
        names = _drive_names(item.get("path") for item in not_read)
        return _answer("ready", reason=(
            f"{_listed(names)} {'is' if len(names) == 1 else 'are'} mounted over the network, "
            f"and Studio Hub never reads network volumes."), not_read=not_read)
    wanted = " or ".join(f"{prefix}*" for prefix in KIT_VOLUME_PREFIXES)
    return _answer("ready", reason=(
        f"No drive named {wanted} is mounted. Studio Hub looks only inside a drive with that "
        f"name; other drives and network volumes are not read, so macOS is never asked about "
        f"them."))


def probe(*, timeout: float | None = None, command: list[str] | None = None) -> dict:
    """Run the child once and answer ``ready`` or ``needs_permission``.

    Never raises: a timeout, a refused read, a child that dies and an unreadable answer are all
    ``needs_permission`` with the reason stated. That is the honest state - "I could not read the
    volumes" must not be reported as "no kit is mounted", which is what an empty list would say.
    """
    limit = PROBE_TIMEOUT_SECONDS if timeout is None else float(timeout)
    try:
        done = subprocess.run(command or _command(), capture_output=True, text=True, timeout=limit)
    except subprocess.TimeoutExpired:
        return _answer("needs_permission", reason=(
            f"The check for removable volumes needs permission: macOS did not answer within "
            f"{limit:g} s. Click Allow on the macOS prompt for Removable Volumes, then restart "
            f"this Mac's Hub services so it can look again."))
    except (OSError, ValueError) as exc:
        return _answer("needs_permission", reason=(
            f"The check for removable volumes could not run: {exc}"))
    lines = [line for line in (done.stdout or "").splitlines() if line.startswith(_PROBE_PREFIX)]
    if done.returncode != 0 or not lines:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        return _answer("needs_permission", reason=(
            f"The check for removable volumes ended without an answer"
            f"{': ' + detail[-1] if detail else ''}."))
    try:
        payload = json.loads(lines[-1][len(_PROBE_PREFIX):])
    except ValueError as exc:
        return _answer("needs_permission", reason=(
            f"The check for removable volumes answered something unreadable: {exc}"))
    if not payload.get("ok"):
        return _answer("needs_permission", reason=str(payload.get("reason") or "the volumes could "
                                                       "not be read"))
    kits = [str(item) for item in payload.get("kits") or [] if isinstance(item, str)]
    looked_inside = [str(item) for item in payload.get("looked_inside") or []
                     if isinstance(item, str)]
    unreadable = [{"path": str(item.get("path")), "reason": str(item.get("reason"))}
                  for item in payload.get("unreadable") or [] if isinstance(item, dict)]
    not_read = [{"path": str(item.get("path")), "reason": str(item.get("reason"))}
                for item in payload.get("not_read") or [] if isinstance(item, dict)]
    return _decide(kits=kits, looked_inside=looked_inside, unreadable=unreadable,
                   not_read=not_read)


def _store(result: dict) -> dict:
    global _STATE
    with _LOCK:
        _STATE = result
        return _copy(_STATE)


def _copy(state: dict) -> dict:
    return {**state, "kits": list(state.get("kits") or []),
            "looked_inside": list(state.get("looked_inside") or []),
            "unreadable": [dict(item) for item in state.get("unreadable") or []],
            "not_read": [dict(item) for item in state.get("not_read") or []]}


def _run() -> None:
    try:
        result = probe()
    except Exception as exc:  # noqa: BLE001 - a probe thread must never die silently
        log.exception("the removable-volume check failed")
        result = _answer("needs_permission", reason=f"The check for removable volumes failed: {exc}")
    _store(result)


def start() -> None:
    """Start the one probe this process will run. Safe to call more than once.

    Called at Hub start-up. It returns immediately; the child is waited for on a daemon thread.
    """
    global _THREAD
    with _LOCK:
        if _STATE is not None:
            return
        if _THREAD is not None and _THREAD.is_alive():
            return
        _THREAD = threading.Thread(target=_run, name="removable-volumes", daemon=True)
        _THREAD.start()


def status() -> dict:
    """The cached answer. **Never probes and never blocks.**

    ``not_checked`` and ``checking`` are their own states, kept apart from ``needs_permission`` and
    from ``ready``: "I have not looked" must not read as "there is nothing there".
    """
    with _LOCK:
        if _STATE is not None:
            return _copy(_STATE)
        if _THREAD is not None and _THREAD.is_alive():
            return _answer("checking", reason="the removable-volume check is running")
        return _answer("not_checked", reason="the removable-volume check has not run")


def kits() -> list[Path]:
    """Every mounted kit library from the cached answer. Empty until the probe has run."""
    return [Path(item) for item in status().get("kits") or []]


# ── Test seams ────────────────────────────────────────────────────────────────────────────────
# The owner's failure is an unanswered macOS prompt, which cannot be reproduced with `tccutil`
# (and must not be tried on a working Mac). A test substitutes a child that blocks - the same
# shape as the prompt - and proves the routes keep answering while it hangs.

def _set_probe_command_for_testing(command: list[str] | None) -> None:
    global _PROBE_COMMAND
    _PROBE_COMMAND = list(command) if command else None


def _reset_for_testing() -> None:
    """Forget the cached answer and any thread, so a test starts from ``not_checked``."""
    global _THREAD, _STATE
    with _LOCK:
        _THREAD = None
        _STATE = None
