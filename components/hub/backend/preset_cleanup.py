"""The one-time preset cleanup for Macs that are already set up (owner decision 2026-10-28).

A Mac set up before the install screen exists carries whatever the kit copied at the time. The
owner's addition is a **one-time** tidying: move the models that are not in that Mac's preset into
the library's ``_trash``, where they can be restored for 30 days and then reach macOS Trash.

Everything about this module is chosen so nothing can happen that the owner has not seen:

1. **A preset per Mac, and the default is the starter for its memory.** ``preset_for`` answers the
   preset the owner set, else the one the Mac's tier implies. **Nothing here derives a preset from
   what a Mac happens to hold** - that would make the target move under the diff.
2. **Dry run first, and it moves nothing.** ``plan`` returns, per Mac, exactly what would move with
   the bytes it frees, every model refused with the reason, and the totals. ``apply`` calls
   ``plan`` itself and moves only on that answer, so the report the owner approves and the work that
   happens are one computation, not two that can disagree.
3. **Refused, not skipped silently**: a model with an active job, a model a lane is serving or has
   assigned, and a model another kept package requires all stay where they are and are reported by
   name. A model the removal would refuse for any other reason is reported the same way.
4. **Moved packages go to the library's `_trash` with a receipt**, through the same move `P4`'s
   removal uses - so restore and the 30-day handoff are that module's, not a second copy of them.
5. **It runs once and records that it ran.** ``<data dir>/preset_cleanup.json`` holds one entry per
   Mac (its own id, or the machine a controller is tidying), written **only by an apply that ran**.
   A later start - or a second apply - is a no-op that says so, and nothing re-runs it by itself.

Read-only to everything except a refused-nothing apply: `plan` writes nothing at all.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import model_removal

#: The file that records, per Mac, that the cleanup has run. Owner-only, one object.
STATE_FILE = "preset_cleanup.json"

#: The key an entry is filed under for a Mac that has no id yet (a Mac that has never joined).
LOCAL = "local"


class CleanupRefused(PermissionError):
    def __init__(self, status: int, code: str, detail: str, **extra) -> None:
        super().__init__(detail)
        self.status = status
        self.body = {"ok": False, "code": code, "detail": detail, **extra}


def state_path(data_dir: Path) -> Path:
    return Path(data_dir) / STATE_FILE


def read_state(data_dir: Path) -> dict:
    """`{machine: {"ran_at", "ran_at_iso", "moved", "bytes", "preset_id", "approved_by"}}`.

    A file that cannot be read is an **empty** state, not a corrupt one: the next apply records
    itself over it, and the alternative - refusing every cleanup because one file was damaged -
    would leave a Mac that needs the tidying unable to have it.
    """
    try:
        value = json.loads(state_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_state(data_dir: Path, value: dict) -> None:
    path = state_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def has_run(data_dir: Path, machine: str) -> dict | None:
    """The record for this Mac, or None. A present record is what makes a second run a no-op."""
    return read_state(data_dir).get(machine) or read_state(data_dir).get(machine.lower())


def preset_for(memory_gb: float | None, chosen: str | None) -> str | None:
    """The preset this Mac's cleanup targets: the owner's choice, else the starter for its memory.

    **Not** "the preset that matches what it holds": a target derived from the current library makes
    every model look in or out depending on itself. A Mac with an unreadable memory figure and no
    choice has no target, and the cleanup refuses rather than inventing 8 GB.
    """
    if chosen:
        return chosen
    from components import model_presets
    return model_presets.starting_id(memory_gb)


def plan(*, library: Path, manifests: dict[str, dict], catalog: dict[str, dict],
         records: dict[str, dict], preset_id: str | None, memory_gb: float | None,
         running_jobs: list[tuple[str, str]] | None = None, paused: dict[str, dict] | None = None,
         keep: set[str] | None = None) -> dict:
    """What a cleanup of this Mac would do. **Moves nothing and writes nothing.**

    `keep` is a set of package ids that must stay whatever the preset says - the owner's own
    allowance, and the answer to "I want this one model for a local test". A model in `keep` is
    reported as kept, not quietly left out.

    The answer is the artifact the owner approves: per model, whether it moves or stays and why;
    the two totals; and the preset it was computed against. `apply` runs this same function, so
    there is no second computation that can disagree with the report.
    """
    from components import model_presets
    if not preset_id:
        raise CleanupRefused(409, "NO_PRESET", (
            "This Mac has no preset to clean up against: the Hub could not read its memory and no "
            "preset was chosen. Choose one for this Mac first; nothing was moved."))
    preset = model_presets.by_id(preset_id)
    wanted = set(model_presets.install_set(preset, manifests))
    keep = set(keep or ())
    in_preset: list[dict] = []
    to_move: list[dict] = []
    kept: list[dict] = []
    refused: list[dict] = []
    for package_id in sorted(records):
        manifest = manifests.get(package_id)
        if manifest is None or manifest.get("_manifest_error"):
            refused.append({"package_id": package_id, "reason":
                            "no usable manifest for this package, so it cannot be judged"})
            continue
        row = {"package_id": package_id, "label": manifest.get("display_name") or package_id,
               "bytes": model_removal.folder_bytes(
                   model_removal.package_folder(library, manifest, records.get(package_id))
                   or Path(library) / package_id)}
        if package_id in wanted:
            in_preset.append(row)
            continue
        if package_id in keep:
            kept.append({**row, "reason": "kept by the owner for this Mac"})
            continue
        # Everything that would refuse a removal refuses here too, per model, so the dry run is the
        # same answer the move would give rather than an optimistic one.
        try:
            holders = model_removal.readers(manifest=manifest, catalog=catalog, records=records)
            holders += model_removal.runtime_readers(
                manifest, running_jobs, (paused or {}).get(package_id))
            if holders:
                refused.append({**row, "reason": "; ".join(holders)})
                continue
            if model_removal.package_folder(library, manifest, records.get(package_id)) is None:
                refused.append({**row, "reason": "its folder is not in this library"})
                continue
        except (OSError, ValueError) as exc:
            refused.append({**row, "reason": f"could not be checked: {exc}"})
            continue
        to_move.append(row)
    return {
        "preset_id": preset["preset_id"],
        "preset_name": preset["name"],
        "memory_gb": memory_gb,
        "in_preset": in_preset,
        "would_move": to_move,
        "kept": kept,
        "refused": refused,
        "move_count": len(to_move),
        "move_bytes": sum(row["bytes"] for row in to_move),
        "in_preset_count": len(in_preset),
        "ran": False,
    }


def apply(*, data_dir: Path, machine: str, library: Path, manifests: dict[str, dict],
          catalog: dict[str, dict], records: dict[str, dict], may_move: bool,
          refusal: str | None = None, preset_id: str | None, memory_gb: float | None,
          approved_by: str, running_jobs: list[tuple[str, str]] | None = None,
          paused: dict[str, dict] | None = None, keep: set[str] | None = None,
          now: float | None = None) -> dict:
    """Run the cleanup once for this Mac, and record that it ran.

    A second call is a **no-op that says so**: the record is read first, so a controller that
    retries a relay, or a Gateway that presses "Clean up all" twice, does not move a second set.
    Nothing about the record is written unless a cleanup really ran - a plan that moved nothing
    because this Mac was already clean still counts as having run, because it did its job.

    The report this returns carries the plan it executed, so the owner sees what happened against
    what was shown.
    """
    moment = time.time() if now is None else float(now)
    previous = has_run(data_dir, machine)
    if previous:
        return {"ok": True, "ran": False, "already_ran_at": previous.get("ran_at"),
                "preset_id": previous.get("preset_id"), "moved": previous.get("moved") or [],
                "moved_bytes": previous.get("bytes") or 0,
                "detail": ("This Mac's one-time cleanup already ran on "
                           f"{previous.get('ran_at_iso') or 'an earlier date'}. Nothing was moved "
                           "again; the models it moved are in the library's trash until then.")}
    report = plan(library=library, manifests=manifests, catalog=catalog, records=records,
                  preset_id=preset_id, memory_gb=memory_gb, running_jobs=running_jobs,
                  paused=paused, keep=keep)
    if not may_move:
        raise CleanupRefused(403, "NOT_THIS_HUBS_LIBRARY", refusal or (
            "This Hub does not own that model library, so it will not move anything out of it."))
    moved: list[str] = []
    failures: list[dict] = []
    for row in report["would_move"]:
        manifest = manifests[row["package_id"]]
        try:
            model_removal.remove(
                library=library, manifest=manifest, catalog=catalog, records=records,
                may_move=True, running_jobs=running_jobs, paused=(paused or {}).get(row["package_id"]),
                reason=f"one-time preset cleanup against {report['preset_name']}", now=moment)
            moved.append(row["package_id"])
        except model_removal.RemoveRefused as exc:
            failures.append({"package_id": row["package_id"], "reason": exc.body["detail"]})
    state = read_state(data_dir)
    entry = {
        "ran_at": moment,
        "ran_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(moment)),
        "preset_id": report["preset_id"],
        "approved_by": approved_by,
        "moved": moved,
        "bytes": sum(row["bytes"] for row in report["would_move"]
                     if row["package_id"] in moved),
        "failed": failures,
    }
    state[machine] = entry
    _write_state(data_dir, state)
    return {"ok": not failures, "ran": True, **entry, "plan": report,
            "detail": (f"Moved {len(moved)} model(s) out of the preset {report['preset_name']} and "
                       f"into the library's trash. They can be restored for "
                       f"{model_removal.REMOVED_MODEL_DAYS} days.")}
