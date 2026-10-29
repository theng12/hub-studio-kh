"""Moving a model package out of a library: a real removal that cannot destroy anything.

**Owner decision 2026-10-28.** The Models page's Remove was a stub ("unavailable until it can
protect models with active readers"), so the only way to get 9 GB back was Finder - which deletes
without a receipt, without a receipt update, and without knowing whether a job was reading it.
This module is the real thing, and everything about it is chosen to be undoable:

* **A move, never a delete.** The package folder goes to ``<library>/_trash/<package>-<stamp>/`` -
  the same folder a kit prune uses - with a ``manifest.json`` beside it recording what it was, when
  and why. The space comes back when the operator empties that folder, which is his call.
* **Refused while anything is using it.** A running job that names the model, a paused model whose
  Studio still holds its weights, or a package another installed package requires all refuse the
  move and say which. The floor for "in use" is deliberately generous: a false refusal costs one
  message, a false removal costs a 9 GB download and possibly a broken generation.
* **The receipt goes with it.** A receipt whose folder is gone makes the app read the package as
  "installed under a different manifest" and offer a verify - the wrong advice. `forget_receipts`
  drops it in the same breath, exactly as retirement and the kit's prune do.
* **Ownership first.** `library_ownership` decides whether this Hub may move anything out at all;
  a Hub started over a library it does not own refuses rather than moving real weights.

Read-only to everything except the package's own folder and the receipt index.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

#: The Hub's Trash Folder machinery. Imported at module level so the 30-day handoff below calls the
#: **same** `_macos_trash` the Trash Folder's own handoff uses, and so a test can replace that one
#: seam instead of a copy of it.
from .. import output_storage

#: Why a package may not be moved out, each with the reader that answers it.
IN_USE = "IN_USE"

#: Folders in a library that are **never** a package: the Hub's own state, and the trash itself.
#: A damaged receipt naming one of them must not make `package_folder` return the whole trash
#: folder - which `remove` would then move, taking every other removed package with it.
RESERVED = frozenset((".studiohub", "_trash"))

#: Whether this process has moved a package out, or a removal is still waiting for its 30-day
#: handoff. The periodic cleanup asks this **before** it reads the library: a Mac that has never
#: removed anything must not touch the model library at all, because that library is on a removable
#: volume and reading it can wait on a macOS permission prompt (the 0.6.210 freeze).
_REMOVAL_HAPPENED = False
_PENDING_FILE = "model-removal-pending.json"


def _pending_path() -> Path:
    """A marker in the Hub's own data folder - never inside the model library."""
    from . import ledger
    return ledger.DATA_DIR / _PENDING_FILE


def removal_happened() -> bool:
    """True only when a removal is worth reading the library for.

    Either this process moved a package out, or a marker says a removal is still waiting for its
    handoff (so a Hub that restarted before the 30 days were up still finishes the job). With no
    removal of either kind this is False and the cleanup pass reads nothing.
    """
    if _REMOVAL_HAPPENED:
        return True
    try:
        return _pending_path().is_file()
    except OSError:
        return False


def _mark_pending() -> None:
    global _REMOVAL_HAPPENED
    _REMOVAL_HAPPENED = True
    try:
        path = _pending_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"since": time.time()}), encoding="utf-8")
    except OSError:
        # The move itself succeeded; the marker only decides whether a later pass looks again.
        pass


def _clear_pending() -> None:
    global _REMOVAL_HAPPENED
    _REMOVAL_HAPPENED = False
    try:
        _pending_path().unlink()
    except OSError:
        pass


class RemoveRefused(PermissionError):
    """The package cannot be moved out, and this says which of the reasons it is."""

    def __init__(self, status: int, code: str, detail: str, **extra) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.body = {"ok": False, "code": code, "detail": detail, **extra}


def package_folder(library: Path, manifest: dict, record: dict | None = None) -> Path | None:
    """The **package** folder this manifest's bytes live in, or None.

    The unit is the package, not one revision: a library holds ``<package>/<revision>/...``, and
    moving only the revision would leave ``<package>/`` behind - which the app then reads as a
    package that is *there* with nothing in it. So the receipt's own ``folder`` (which names the
    revision, ``<package>/<rev>``) is reduced to its top segment inside the library, exactly as
    `model_packages.import_folder` keeps a folder that was already in the library under its own
    name.

    Two candidates, in the order `model_packages._installed_folder` uses: the receipt's folder
    reduced to its top segment when that is a real folder inside the library, else the
    conventional ``<package_id>``. Every candidate is checked against the library root, so a
    manifest or a damaged receipt cannot name ``..`` or an absolute path.
    """
    root = Path(library)
    for candidate in _candidates(root, manifest, record):
        if _inside(root, candidate) and candidate.is_dir() and not candidate.is_symlink():
            return candidate
    return None


def _candidates(root: Path, manifest: dict, record: dict | None):
    """Every folder a package's bytes could be in, best evidence first."""
    relative = str((record or {}).get("folder") or "").strip()
    parts = [part for part in relative.split("/") if part]
    # `<package>/<revision>` -> `<package>`; a single-segment folder is its own top.
    if (relative and not relative.startswith("/") and "\\" not in relative
            and parts and all(part not in (".", "..") for part in parts)
            and parts[0] not in RESERVED):
        yield root / parts[0]
    package_id = str(manifest.get("package_id") or "")
    if _safe_name(package_id):
        yield root / package_id


def _safe_name(name: str) -> bool:
    """A package id is one folder name: no separator, no dot names, not absolute, not reserved."""
    return (bool(name) and name not in (".", "..") and name not in RESERVED and "/" not in name
            and "\\" not in name and Path(name).name == name and not Path(name).is_absolute())


def _inside(root: Path, candidate: Path) -> bool:
    """Whether `candidate` is the library itself or directly inside it, links resolved."""
    try:
        real_root = root.resolve()
        real = candidate.resolve()
    except OSError:
        return False
    return real == real_root or real.parent == real_root


def folder_bytes(folder: Path) -> int:
    """Bytes on disk under a folder, symlinks not followed and not counted."""
    total = 0
    for path in Path(folder).rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def readers(*, manifest: dict, catalog: dict[str, dict], records: dict[str, dict]) -> list[str]:
    """Everything that would be reading this package right now, in the operator's words.

    Each reason names **who** is holding it, because "a model is in use" is the kind of message
    that sends someone clicking Remove again:
      * another installed package requires it (removing it breaks that one);
      * this package requires another that is **not** installed, so it is already unusable;
      * a job that names its model is running on this Mac.
    A busy *worker* is not itself a reason: the check is about this package's bytes.
    """
    found: list[str] = []
    package_id = str(manifest.get("package_id") or "")
    from components import model_packages
    for other_id, other in sorted(catalog.items()):
        if other_id == package_id or other.get("_manifest_error") or other_id not in records:
            continue
        try:
            needs = model_packages.required_packages(other)
        except ValueError:
            continue
        if package_id in needs:
            found.append(f"{other_id} is installed and requires it")
    return found


def runtime_readers(manifest: dict, running_jobs: list[tuple[str, str]] | None,
                    paused: dict | None) -> list[str]:
    """The live readers: a running job on this model, and a Studio that still holds its weights.

    `running_jobs` is (studio id, **model name**) - the caller resolves the job's own model from
    the same batch record, because a worker job id is not a model name and guessing would unload
    weights under a live generation. A match is on any of the manifest's names (package id, model
    id, aliases) so a batch that names the repo is recognised too.
    """
    found: list[str] = []
    names = {name.lower() for name in _names(manifest)}
    for studio, model in running_jobs or []:
        if str(model).strip().lower() in names:
            found.append(f"a {studio} job is running on {model}")
    if paused:
        found.append(f"{paused.get('studio') or 'a Studio'} paused it after "
                     f"{paused.get('failures') or 'repeated'} failures and still holds its weights")
    return found


def _names(manifest: dict) -> list[str]:
    return [str(value) for value in (manifest.get("package_id"), manifest.get("model_id"),
                                     *(manifest.get("aliases") or [])) if value]


def remove(*, library: Path, manifest: dict, catalog: dict[str, dict], records: dict[str, dict],
           may_move: bool, refusal: str | None = None, running_jobs: list[tuple[str, str]] | None = None,
           paused: dict | None = None, reason: str = "removed from the Models screen",
           now: float | None = None) -> dict:
    """Move one package's folder into the library's ``_trash`` and drop its receipt.

    Every refusal happens **before** anything moves, and each is its own sentence: the ownership
    guard, the in-use readers, a folder that is not there. On success the answer names the folder
    it went to and the bytes it will free when emptied.

    The move is a same-volume `os.rename`, so it cannot leave a half-copy, and it fails loudly on a
    library whose `_trash` is on another volume rather than copying 9 GB under a different name.
    """
    package_id = str(manifest.get("package_id") or "")
    if not package_id:
        raise RemoveRefused(400, "UNKNOWN_PACKAGE", "The manifest names no package.")
    if not may_move:
        raise RemoveRefused(403, "NOT_THIS_HUBS_LIBRARY", refusal or (
            "This Hub does not own that model library, so it will not move anything out of it. "
            "Start the Hub that owns it and remove the model there."))
    holders = readers(manifest=manifest, catalog=catalog, records=records)
    holders += runtime_readers(manifest, running_jobs, paused)
    if holders:
        raise RemoveRefused(409, IN_USE, (
            f"{package_id} is in use, so nothing was moved: " + "; ".join(holders) +
            ". Stop the job (or remove the package that needs it) first, then try again."),
            holders=holders)
    folder = package_folder(library, manifest, records.get(package_id))
    if folder is None:
        raise RemoveRefused(404, "NOT_INSTALLED", (
            f"{package_id} is not in this library, so there is nothing to remove."))
    size = folder_bytes(folder)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(time.time() if now is None else float(now)))
    trash = Path(library) / "_trash"
    target = trash / f"{package_id}-{stamp}"
    suffix = 1
    while os.path.lexists(target):
        # Two removals inside one second must not merge: the second gets its own folder rather
        # than replacing the first, which would lose the bytes the first one moved.
        suffix += 1
        target = trash / f"{package_id}-{stamp}-{suffix}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(folder, target)
    except OSError as exc:
        raise RemoveRefused(409, "MOVE_FAILED", (
            f"{package_id} could not be moved to {trash}: {exc}. Nothing was changed and nothing "
            f"was deleted.")) from exc
    receipt = _write_receipt(target, manifest, size, reason, now)
    _mark_pending()
    from components import model_packages
    forgotten: list[str] = []
    try:
        forgotten = model_packages.forget_packages(library, [package_id])
    except (OSError, model_packages.PackageError):
        forgotten = []                       # the folder is gone, so the row reads "not installed"
    return {"ok": True, "package_id": package_id, "moved_to": str(target), "bytes": size,
            "receipt": str(receipt), "receipts_dropped": forgotten,
            "detail": (f"Moved {package_id} ({size / 1e9:.1f} GB) to {trash}. The space comes back "
                       f"when that folder is emptied; nothing was deleted.")}


def _write_receipt(target: Path, manifest: dict, size: int, reason: str,
                   now: float | None) -> Path:
    """What this folder is and when it got here, so a restore is a fact and not an inference."""
    moment = time.time() if now is None else float(now)
    path = target / ".studiohub-removed.json"
    path.write_text(json.dumps({
        "package_id": manifest.get("package_id"),
        "model_id": manifest.get("model_id"),
        "revision": manifest.get("revision"),
        "engine": manifest.get("engine"),
        "bytes": size,
        "removed_at": moment,
        "removed_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(moment)),
        "reason": reason,
        "restore": ("Move this folder back to its package id in the library and run Verify Files "
                    "in Studio Hub, or re-install the package from the Models screen."),
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def listing(library: Path) -> list[dict]:
    """Every removed package still in the library's ``_trash``, newest first, with its age.

    Reads each folder's own receipt, so a folder that was moved by a **kit** prune (which writes
    none) is still listed - with `receipt: false` and its age taken from the folder. That is the
    "restore within 30 days" surface: nothing here deletes, and an entry with no receipt says so
    rather than being hidden.
    """
    root = Path(library) / "_trash"
    if root.is_symlink() or not root.is_dir():
        return []
    rows: list[dict] = []
    for folder in root.iterdir():
        if folder.is_symlink() or not folder.is_dir() or folder.name.startswith("."):
            continue
        record: dict = {}
        receipt = folder / ".studiohub-removed.json"
        if receipt.is_file() and not receipt.is_symlink():
            try:
                record = json.loads(receipt.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                record = {}
        try:
            stamp = folder.stat().st_mtime
        except OSError:
            stamp = None
        rows.append({
            "folder": str(folder),
            "name": folder.name,
            "package_id": record.get("package_id") or _unstamped(folder.name),
            "bytes": record.get("bytes") if isinstance(record.get("bytes"), int) else folder_bytes(folder),
            "removed_at": record.get("removed_at") or stamp,
            "receipt": bool(record),
            "reason": record.get("reason"),
        })
    return sorted(rows, key=lambda row: row.get("removed_at") or 0, reverse=True)


# ── Restore, and the 30-day handoff ───────────────────────────────────────────────────────────────
# The owner's addition (2026-10-28): "moved packages go to the library's _trash with a manifest of
# what/when, auto-deleted after 30 days (reuse the Hub/Gateway trash flow if it has it)". That flow
# hands a Trash Folder item to **macOS Trash** after `trash_handoff_days` and never deletes; it is
# the right mechanism, so it is reused rather than duplicated - but its `_trash_items` reads only the
# Hub's ``<trash>/<date>/<name>`` layout, and a library's `_trash` is flat. These two functions are
# the model half: the same "hand it to macOS Trash, never delete" rule, over the library's folders.

#: How long a removed model stays recoverable in the library's `_trash` before it is handed to macOS
#: Trash. The owner's number; macOS owns the final deletion from there (its own 30-day setting).
REMOVED_MODEL_DAYS = 30


def restore(library: Path, folder: Path, package_id: str | None = None) -> dict:
    """Put a removed package back where the Hub looks for it.

    **A restore is a move with the receipt checked**, not a copy: the folder is renamed back to
    ``<library>/<package_id>``, and the caller then runs Verify Files (or the install screen does)
    to write a fresh receipt. The package id comes from the removed folder's own receipt when it
    has one - a folder moved by a kit prune carries none, so `package_id` is required then and the
    name is used as a last resort.

    Refuses to overwrite: a package folder already at the destination means something is installed
    there, and replacing it would be the removal in reverse.
    """
    root = Path(library)
    source = Path(folder)
    trash = root / "_trash"
    # Anchor at the library: only a direct child of its own `_trash` may be restored, never a path
    # that walks out of it or a link pointing elsewhere.
    try:
        if source.is_symlink() or not source.is_dir():
            raise RemoveRefused(404, "NOT_REMOVED", f"{source} is not a removed model folder.")
        if source.parent.resolve() != trash.resolve():
            raise RemoveRefused(400, "BAD_FOLDER",
                                f"{source} is not directly inside {trash}.")
    except OSError as exc:
        raise RemoveRefused(404, "NOT_REMOVED", f"{source} could not be read: {exc}") from exc
    name = str(package_id or "").strip()
    if not name:
        receipt = source / ".studiohub-removed.json"
        if receipt.is_file() and not receipt.is_symlink():
            try:
                name = str(json.loads(receipt.read_text(encoding="utf-8")).get("package_id") or "")
            except (OSError, ValueError):
                name = ""
    if not name:
        name = _unstamped(source.name)
    if (not name or name in (".", "..") or "/" in name or "\\" in name
            or Path(name).name != name):
        raise RemoveRefused(400, "BAD_PACKAGE", f"Cannot tell which package {source.name} is.")
    target = root / name
    if os.path.lexists(target):
        raise RemoveRefused(409, "ALREADY_INSTALLED", (
            f"{target} already exists, so {name} was not restored over it. Remove the installed "
            f"copy (or move it aside) first."))
    try:
        os.rename(source, target)
    except OSError as exc:
        raise RemoveRefused(409, "RESTORE_FAILED", (
            f"{name} could not be moved back to {target}: {exc}. It is still in {trash}, unchanged."
        )) from exc
    # The receipt that described it as removed belongs to the trash; dropping it means a folder
    # restored this way cannot later be read as removed at the wrong moment.
    try:
        (target / ".studiohub-removed.json").unlink()
    except OSError:
        pass
    return {"ok": True, "package_id": name, "restored_to": str(target),
            "detail": (f"{name} is back in {root}. Run Verify Files (or the install screen) so the "
                       f"Hub records it again; nothing else was changed.")}


#: `<package>-YYYYMMDD-HHMMSS` and its `-N` disambiguator, anchored at the end of the name.
_STAMP = re.compile(r"-\d{8}-\d{6}(?:-\d+)?$")


def _unstamped(folder_name: str) -> str:
    """A package id from a removed folder's name: `<package>-YYYYMMDD-HHMMSS[-N]` -> `<package>`.

    Both writers use that shape - `remove` below and the kit's `move_to_trash` - so a folder with no
    receipt is still restorable. Anchored at the end of the name, because a package id may itself
    contain a dash (`z-image-turbo-mflux-4bit`) and a `rsplit("-", 1)` would cut one character off
    it. A name that does not carry the stamp is returned unchanged rather than guessed at.
    """
    return _STAMP.sub("", folder_name)


def handoff(library: Path | None = None, days: int = REMOVED_MODEL_DAYS,
            now: float | None = None) -> dict:
    """Hand removed models older than `days` to **macOS Trash**, exactly as the Trash Folder does.

    Called with no argument by the Hub's cleanup pass, which resolves the library from the running
    Hub (`STUDIOHUB_MODELS_DIR`); a Hub with no library has nothing removed to hand over.

    One mechanism for "old enough to leave", not a second deleter: `output_storage._macos_trash`
    is the same call the Trash Folder's own handoff uses, so the owner has one place to empty and
    macOS owns the final deletion. A model that is younger than `days` is left alone and listed as
    kept; nothing is ever removed from disk here.
    """
    moment = time.time() if now is None else float(now)
    cutoff = float(days) * 86400 if isinstance(days, int) and not isinstance(days, bool) and days > 0 else None
    if library is None:
        value = os.environ.get("STUDIOHUB_MODELS_DIR", "").strip()
        if not value:
            return {"ok": True, "moved": [], "kept": [], "failed": [], "cutoff_seconds": cutoff,
                    "skipped": "this Hub has no model library configured"}
        library = Path(value)
    if cutoff is None:
        return {"ok": True, "moved": [], "kept": [row["name"] for row in listing(library)],
                "failed": [], "cutoff_seconds": None}
    entries = listing(library)
    if not entries:
        # Nothing is waiting any more: stop the cleanup pass from reading this library on every
        # tick until the next real removal.
        _clear_pending()
        return {"ok": True, "moved": [], "kept": [], "failed": [], "cutoff_seconds": cutoff}
    old = [row for row in entries if row.get("removed_at") and moment - float(row["removed_at"]) >= cutoff]
    kept = [row["name"] for row in entries if row not in old]
    if not old:
        return {"ok": True, "moved": [], "kept": kept, "failed": [], "cutoff_seconds": cutoff}
    reasons = output_storage._macos_trash([row["folder"] for row in old])
    moved, failed = [], []
    for row, reason in zip(old, reasons):
        if os.path.lexists(row["folder"]):
            failed.append({"folder": row["folder"], "error": reason if reason != "ok" else "still there"})
        else:
            moved.append(row["name"])
    return {"ok": not failed, "moved": moved, "kept": kept, "failed": failed,
            "cutoff_seconds": cutoff}
