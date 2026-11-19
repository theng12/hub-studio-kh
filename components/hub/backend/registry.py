"""Studio registry — host-aware local fleet inventory.

Defaults cover the production Image and Voice Studios. A per-machine `studios.json` at the
launcher root (gitignored) can override any default by `id` or add extra
entries — including studios on OTHER machines over LAN/Tailscale, which is the
foundation for federation and Swarm Batch worker pools.

studios.json format (all fields optional except id for overrides):
[
  { "id": "image", "host": "192.0.2.10", "machine": "studio-2" },
  { "id": "image-b", "modality": "image", "host": "192.0.2.10", "port": 47868,
    "machine": "studio-2", "title": "Image Studio (M2 Ultra)" }
]
"""

import json
import math
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from components import preferred_hold_clock

from .vocabulary import NODE_MODALITIES

# Launcher root = two levels up from this file (app/backend/registry.py)
LAUNCHER_ROOT = Path(__file__).resolve().parents[2]

# Where mutable per-machine STATE lives (studios.json, hub.db, .hub_token,
# .fleet_token, machine_labels.json, hub_state.json, uploads/). Defaults to the
# launcher root — no behavior change — but overridable via STUDIOHUB_DATA_DIR so
# tests (and alternate deployments) can point state elsewhere. Code files
# (VERSION, frontend, the api/ tree for control) always stay under LAUNCHER_ROOT.
import os as _os
DATA_DIR = (Path(_os.environ["STUDIOHUB_DATA_DIR"]).resolve()
            if _os.environ.get("STUDIOHUB_DATA_DIR") else LAUNCHER_ROOT)
DATA_DIR.mkdir(parents=True, exist_ok=True)

REGISTRY_FILE = DATA_DIR / "studios.json"
LABELS_FILE = DATA_DIR / "machine_labels.json"
FLAGS_FILE = DATA_DIR / "machine_flags.json"

# machine-key -> friendly display name. The KEY (e.g. "local", "imac-pdt") is
# the technical identity used for control routing and studio ids; the label is
# purely cosmetic, so renaming never breaks anything.
_labels_cache: dict | None = None

# machine-key -> {"enabled": bool, "studios": {studio-id: {"enabled": bool,
# "removed": bool}}}.
# Disabled machines and Studios stay registered and monitored; only new job
# dispatch is paused. Missing entries default to enabled, which keeps existing
# machine_flags.json files backward compatible.
_flags_cache: dict | None = None


def load_flags() -> dict:
    global _flags_cache
    if _flags_cache is None:
        try:
            _flags_cache = json.loads(FLAGS_FILE.read_text()) if FLAGS_FILE.exists() else {}
        except (json.JSONDecodeError, OSError):
            _flags_cache = {}
    return _flags_cache


def machine_enabled(machine: str) -> bool:
    return bool(load_flags().get(machine, {}).get("enabled", True))


def normalize_priority_order(value: object) -> list[str]:
    """Read one canonical priority list; damaged explicit state is neutral."""
    if not isinstance(value, list):
        return []
    from .vocabulary import PRIORITY_MODALITIES
    if (any(not isinstance(item, str) or item not in PRIORITY_MODALITIES for item in value)
            or len(value) != len(set(value))):
        return []
    return list(value)


def priority_order(machine: str) -> list[str]:
    row = load_flags().get(machine, {})
    if not isinstance(row, dict):
        return []
    if "priority_order" in row:
        return normalize_priority_order(row.get("priority_order"))
    return ["chat"] if row.get("chat_priority") is True else []


def with_render_default(order: list[str]) -> list[str]:
    """One saved order with `vocabulary.DEFAULT_PRIORITY_ORDER` applied — to a **non-empty** order.

    The single place that rule is written. A joined peer's order arrives as a list rather than
    from this Mac's flags (`broker.priority_order`), and it must read the same way.

    **An empty order stays empty.** "No preference" is a distinct, deliberate state with its own
    meaning, spelled out in the app: every enabled function is equal and the oldest eligible
    queued work runs. It is not a Mac that preferred something and did not know render existed, so
    it is not a place to insert a rank the owner never gave — and `tests/
    test_render_physical_admission.py` pins exactly that (a newer render does not jump older
    equal-priority work). A Mac that *did* state a preference but omitted render is the case the
    default is for: its old behaviour was render first (`broker._pending_render_for_machine`
    reserved the Mac for it), so it keeps that behaviour without anything being rewritten.
    """
    from .vocabulary import DEFAULT_PRIORITY_ORDER
    missing = [kind for kind in DEFAULT_PRIORITY_ORDER if kind not in order]
    if not order or not missing:
        return list(order)
    return [*missing, *order]


def chat_priority(machine: str) -> bool:
    """Compatibility projection for clients that only understand LLM-first."""
    order = priority_order(machine)
    return bool(order and order[0] == "chat")


def set_priority_order(machine: str, order: list[str]) -> None:
    from .vocabulary import PRIORITY_MODALITIES
    if (not isinstance(order, list)
            or any(not isinstance(item, str) or item not in PRIORITY_MODALITIES for item in order)
            or len(order) != len(set(order))):
        raise ValueError("priority_order must contain unique canonical modalities")
    global _flags_cache
    flags = dict(load_flags())
    saved = flags.get(machine, {})
    row = dict(saved) if isinstance(saved, dict) else {}
    row["priority_order"] = list(order)
    row.pop("chat_priority", None)
    flags[machine] = row
    descriptor, name = tempfile.mkstemp(prefix=f".{FLAGS_FILE.name}-", dir=FLAGS_FILE.parent)
    temporary = Path(name)
    try:
        with _os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(flags, handle, indent=2)
            handle.write("\n")
            handle.flush()
            _os.fsync(handle.fileno())
        temporary.replace(FLAGS_FILE)
    except BaseException:
        # A failed policy write is evidence worth retaining, and project removals
        # are recoverable. Move the orphan into the Hub's one Trash Folder instead
        # of a second `_trash` root (owner request 2026-10-15).
        if temporary.exists():
            from .. import output_storage
            output_storage.retire(temporary, "failed-write")
        raise
    _flags_cache = flags


def set_chat_priority(machine: str, enabled: bool) -> None:
    """Legacy bool-only write adapter."""
    set_priority_order(machine, ["chat"] if enabled else [])


# **One on/off switch per function, ON by default** (owner, 2026-10-28: "each generation should
# actually have a cross to switch them on or off. but defaults to on.").
#
# Stored per Mac beside `priority_order`, as `{kind: bool}` under one key. It says whether this Mac
# takes that kind of work at all — a rank decides *when*, this decides *whether* — and it is not the
# per-Studio switch (`studio_enabled`, the Studios list): a Studio row is a worker's own pause, this
# is the work order's own list, and they answer different questions.
#
# **Absent = on, and only a real boolean `false` is off.** Every existing `machine_flags.json` row,
# every Mac that never opened this control, and a hand-edited or half-written value all keep taking
# work exactly as they did, and a malformed row can only ever keep the safe (previous) answer.
FUNCTION_ENABLED_KEY = "function_enabled"


def normalize_function_enabled(value: object) -> dict[str, bool]:
    """One saved switch map, cut to real booleans on real rankable functions."""
    if not isinstance(value, dict):
        return {}
    from .vocabulary import PRIORITY_MODALITIES
    return {kind: on for kind, on in value.items()
            if isinstance(kind, str) and kind in PRIORITY_MODALITIES and type(on) is bool}


def function_switches(machine: str) -> dict[str, bool]:
    """This Mac's saved switches, exactly as stored (nothing filled in)."""
    row = load_flags().get(machine, {})
    return normalize_function_enabled(row.get(FUNCTION_ENABLED_KEY)) if isinstance(row, dict) else {}


def function_enabled(machine: str, kind: str) -> bool:
    """Whether this Mac takes `kind` at all. Absent, malformed or non-boolean reads as ON."""
    return function_switches(machine).get(kind) is not False


def set_function_enabled(machine: str, kind: str, enabled: bool) -> None:
    """Record the owner's switch for one function on one Mac (the same atomic write as the rest)."""
    from .vocabulary import PRIORITY_MODALITIES
    if kind not in PRIORITY_MODALITIES:
        raise ValueError("function must be a rankable function")
    if type(enabled) is not bool:
        raise ValueError("enabled must be true or false")
    flags = dict(load_flags())
    saved = flags.get(machine, {})
    row = dict(saved) if isinstance(saved, dict) else {}
    switches = normalize_function_enabled(row.get(FUNCTION_ENABLED_KEY))
    switches[kind] = enabled
    row[FUNCTION_ENABLED_KEY] = switches
    flags[machine] = row
    _write_flags(flags)


# **Render while paused — the owner's per-Mac exemption (owner, 2026-10-28).**
#
# A paused Mac gets no new work of any kind, which is the rule and stays the rule. The owner's
# own question was narrower: may a Mac he paused *for generation* still assemble the final
# videos it is opted in to render? Yes, but only where he says so, per Mac, and never by
# inference.
#
# OFF by default, and off is byte-identical to before: a paused Mac takes no render either. A
# strict reader — absent, malformed, or not a real boolean `true` is **off** — so a hand-edited
# or half-written row can only ever keep the safe answer.
RENDER_WHILE_PAUSED_KEY = "render_while_paused"


def render_while_paused(machine: str) -> bool:
    """Whether this Mac's own pause exempts the final-render lane."""
    row = load_flags().get(machine, {})
    if not isinstance(row, dict):
        return False
    return row.get(RENDER_WHILE_PAUSED_KEY) is True


def set_render_while_paused(machine: str, enabled: bool) -> None:
    """Record the owner's choice for one Mac (the same atomic write as every other flag)."""
    if type(enabled) is not bool:
        raise ValueError("render_while_paused must be true or false")
    global _flags_cache
    flags = dict(load_flags())
    saved = flags.get(machine, {})
    row = dict(saved) if isinstance(saved, dict) else {}
    row[RENDER_WHILE_PAUSED_KEY] = enabled
    flags[machine] = row
    descriptor, name = tempfile.mkstemp(prefix=f".{FLAGS_FILE.name}-", dir=FLAGS_FILE.parent)
    temporary = Path(name)
    try:
        with _os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(flags, handle, indent=2)
            handle.write("\n")
            handle.flush()
            _os.fsync(handle.fileno())
        temporary.replace(FLAGS_FILE)
    except BaseException:
        if temporary.exists():
            from .. import output_storage
            output_storage.retire(temporary, "failed-write")
        raise
    _flags_cache = flags


# Preferred-work idle hold (owner request, 2026-10-17): one optional per-Mac choice of a
# preferred generation function that wins over ranked priority while demand lasts, then holds the
# Mac for `idle_seconds` after its last preferred job actually finishes before lending the Mac to
# other work. OFF by default; every field is optional so older machine_flags.json rows read as
# the previous behaviour with no migration. The bounds are defined once in the shared clock leaf
# (`components/preferred_hold_clock.py`) and aliased here for the scheduler and API.
PREFERRED_WORK_BOUNDS = preferred_hold_clock.PREFERRED_WORK_BOUNDS
PREFERRED_WORK_DEFAULT_S = preferred_hold_clock.PREFERRED_WORK_DEFAULT_S


def _validate_preferred(value: object) -> dict | None:
    """A saved preference, or ``None`` when absent/malformed (malformed reads as off).

    Only a real boolean ``true`` enables the preference. A truthy non-boolean in a
    hand-edited/malformed row (``1``, ``"yes"``) reads as off, consistent with the worker
    reader and with the API's own bool-only write validation. A legacy row with no
    ``enabled`` key also reads off, exactly as it did before the field existed.
    """
    if not isinstance(value, dict):
        return None
    modality = value.get("modality")
    if not isinstance(modality, str) or modality not in NODE_MODALITIES:
        return None
    seconds = value.get("idle_seconds")
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        seconds = PREFERRED_WORK_DEFAULT_S
    low, high = PREFERRED_WORK_BOUNDS
    seconds = max(low, min(high, int(seconds)))
    return {"enabled": value.get("enabled") is True, "modality": modality,
            "idle_seconds": seconds}


def preferred_work(machine: str) -> dict | None:
    """This Mac's saved idle-hold preference, or ``None`` when off/malformed."""
    row = load_flags().get(machine, {})
    return _validate_preferred(row.get("preferred_work")) if isinstance(row, dict) else None


def _write_flags(flags: dict) -> None:
    """Persist the whole flags map atomically; the cache changes only after the replace."""
    global _flags_cache
    descriptor, name = tempfile.mkstemp(prefix=f".{FLAGS_FILE.name}-", dir=FLAGS_FILE.parent)
    temporary = Path(name)
    try:
        with _os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(flags, handle, indent=2)
            handle.write("\n")
            handle.flush()
            _os.fsync(handle.fileno())
        temporary.replace(FLAGS_FILE)
    except BaseException:
        if temporary.exists():
            trash = DATA_DIR / "_trash" / "failed-writes"
            try:
                trash.mkdir(parents=True, exist_ok=True)
                temporary.replace(trash / temporary.name)
            except OSError:
                pass
        raise
    _flags_cache = flags


def set_preferred_work(machine: str, modality: str | None, idle_seconds: int,
                       *, enabled: bool) -> None:
    """Persist one Mac's preference atomically beside its priority flags.

    ``modality=None`` clears the preference (the caller disables it); a preference is only
    written when the modality is a canonical one. No scheduler state is touched here.
    """
    from .vocabulary import NODE_MODALITIES
    if modality is None:
        payload = None
    else:
        if modality not in NODE_MODALITIES:
            raise ValueError(f"preferred modality {modality!r} is not a registered function")
        low, high = PREFERRED_WORK_BOUNDS
        if not isinstance(idle_seconds, int) or isinstance(idle_seconds, bool) \
                or not low <= idle_seconds <= high:
            raise ValueError(f"idle_seconds must be a whole number of {low}-{high} seconds")
        payload = {"enabled": bool(enabled), "modality": modality,
                   "idle_seconds": int(idle_seconds)}
    flags = dict(load_flags())
    saved = flags.get(machine, {})
    row = dict(saved) if isinstance(saved, dict) else {}
    if payload is None:
        row.pop("preferred_work", None)
        row.pop("preferred_hold", None)
        row.pop("preferred_reservation", None)
    else:
        previous = (saved or {}).get("preferred_work") \
            if isinstance(saved, dict) else None
        # The hold and the in-flight reservation belong to the preference that opened them:
        # clearing or changing the preferred function retires both. Re-saving the same policy
        # (including a duration edit) must not reset a hold or reservation that a real dispatch
        # or completion just wrote, so only a disable or an actual modality change clears them.
        # A malformed persisted value reads as OFF (see _validate_preferred) and cannot own
        # either, so a save over it starts clean instead of inheriting unattributable state;
        # this is also the isinstance guard that keeps the write path from raising on such a row.
        switched = not isinstance(previous, dict) \
            or previous.get("modality") != payload["modality"]
        if not payload["enabled"] or switched:
            row.pop("preferred_hold", None)
            row.pop("preferred_reservation", None)
        row["preferred_work"] = payload
    flags[machine] = row
    _write_flags(flags)


def preferred_hold(machine: str) -> dict | None:
    """The persisted hold window for one Mac, or ``None`` when none is running.

    Written exactly once per preferred-job completion (see
    ``components/hub/backend/preferred_hold.py``): ``ended_wall`` is the wall clock of the last
    preferred finish and ``boot_steady`` is that process's ``time.monotonic()`` at the same
    moment, so the runtime can answer "how much hold is left" without rewriting the file on
    every poll and can bound a clock jump or a restart instead of holding for ever.
    """
    row = load_flags().get(machine, {})
    if not isinstance(row, dict) or not isinstance(row.get("preferred_hold"), dict):
        return None
    hold = row["preferred_hold"]
    try:
        ended_wall = float(hold["ended_wall"])
        boot_steady = float(hold["boot_steady"])
    except (KeyError, OverflowError, TypeError, ValueError):
        return None
    if not (math.isfinite(ended_wall) and math.isfinite(boot_steady)):
        return None
    return {"ended_wall": ended_wall, "boot_steady": boot_steady}


def set_preferred_hold(machine: str, ended_wall: float, boot_steady: float,
                       *, clear_reservation_owner: str | None = None) -> None:
    """Record one hold window's start, or clear it with ``ended_wall=None``.

    When ``clear_reservation_owner`` names a dispatch attempt, the same atomic row write also
    removes that attempt's in-flight reservation — but only while the stored reservation still
    carries that exact owner. A delayed completion from an older attempt therefore cannot
    clear a newer attempt's reservation, while the hold itself keeps the frozen completion
    semantics (a late terminal may still open a scheduling hold). One write, never split.
    """
    flags = dict(load_flags())
    saved = flags.get(machine, {})
    row = dict(saved) if isinstance(saved, dict) else {}
    if clear_reservation_owner:
        reservation = row.get("preferred_reservation")
        if isinstance(reservation, dict) \
                and reservation.get("owner") == clear_reservation_owner:
            row.pop("preferred_reservation", None)
    if ended_wall is None:
        row.pop("preferred_hold", None)
    else:
        row["preferred_hold"] = {"ended_wall": float(ended_wall),
                                 "boot_steady": float(boot_steady)}
    flags[machine] = row
    _write_flags(flags)


def preferred_reservation(machine: str) -> dict | None:
    """The bounded in-flight reservation for one Mac, or ``None`` when absent/malformed.

    The row is worker input, so every numeric field is validated here rather than trusted:
    non-numeric, missing, NaN or infinite stamps/budget read as no reservation (the same
    answer as a missing row), and an expired budget is left to the shared clock math.
    """
    row = load_flags().get(machine, {})
    if not isinstance(row, dict) or not isinstance(row.get("preferred_reservation"), dict):
        return None
    reservation = row["preferred_reservation"]
    modality = reservation.get("modality")
    owner = reservation.get("owner")
    if not isinstance(modality, str) or not modality \
            or not isinstance(owner, str) or not owner:
        return None
    try:
        started_wall = float(reservation["started_wall"])
        started_steady = float(reservation["started_steady"])
        budget = float(reservation["budget_s"])
    except (KeyError, OverflowError, TypeError, ValueError):
        return None
    if not (math.isfinite(started_wall) and math.isfinite(started_steady)
            and math.isfinite(budget) and budget > 0.0):
        return None
    return {"modality": modality, "owner": owner,
            "started_wall": started_wall, "started_steady": started_steady,
            "budget_s": budget}


def set_preferred_reservation(machine: str, modality: str, owner: str,
                              started_wall: float, started_steady: float,
                              budget_s: float) -> None:
    """Write one dispatch attempt's bounded reservation (or replace it on adoption).

    Validation happens before the flags map is read or replaced: a non-finite stamp or a
    non-positive/non-finite budget raises ``ValueError`` with no disk or cache mutation, so
    a corrupt caller can never publish an unbounded exemption.
    """
    modality = str(modality)
    owner = str(owner)
    if not modality or not owner:
        raise ValueError("a preferred reservation needs a modality and an owner")
    try:
        wall = float(started_wall)
        steady = float(started_steady)
        budget = float(budget_s)
    except (OverflowError, TypeError, ValueError):
        raise ValueError("a preferred reservation needs numeric finite stamps and budget") \
            from None
    if not (math.isfinite(wall) and math.isfinite(steady)
            and math.isfinite(budget) and budget > 0.0):
        raise ValueError("a preferred reservation needs finite stamps and a positive budget")
    flags = dict(load_flags())
    saved = flags.get(machine, {})
    row = dict(saved) if isinstance(saved, dict) else {}
    row["preferred_reservation"] = {"modality": modality, "owner": owner,
                                    "started_wall": wall, "started_steady": steady,
                                    "budget_s": budget}
    flags[machine] = row
    _write_flags(flags)


def assigned_image_model(machine: str) -> str | None:
    """The image model the Gateway's image pool assigned to this Mac, or None (no assignment).

    Only work submitted with ``routing: assigned`` reads it: a job that names its model directly
    still runs on any Mac that has the model (owner, 2026-10-24).
    """
    row = load_flags().get(machine, {})
    value = row.get("assigned_image_model") if isinstance(row, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def set_assigned_image_model(machine: str, model: str | None) -> None:
    """Set (or clear, with None) the image pool's model for this Mac."""
    if model is not None and (not isinstance(model, str) or not model.strip() or len(model) > 300):
        raise ValueError("assigned image model must be a model id or null")
    flags = dict(load_flags())
    saved = flags.get(machine, {})
    row = dict(saved) if isinstance(saved, dict) else {}
    if model is None:
        row.pop("assigned_image_model", None)
    else:
        row["assigned_image_model"] = model.strip()
    flags[machine] = row
    _write_flags(flags)


def set_machine_enabled(machine: str, enabled: bool):
    global _flags_cache
    flags = dict(load_flags())
    flags.setdefault(machine, {})["enabled"] = bool(enabled)
    FLAGS_FILE.write_text(json.dumps(flags, indent=2) + "\n")
    _flags_cache = flags


def studio_enabled(machine: str, studio_id: str) -> bool:
    """Whether one registered Studio may receive new scheduled work."""
    machine_flags = load_flags().get(machine, {})
    studio_flags = (
        machine_flags.get("studios", {})
        if isinstance(machine_flags, dict) else {}
    )
    row = studio_flags.get(studio_id, {}) if isinstance(studio_flags, dict) else {}
    return bool(row.get("enabled", True)) if isinstance(row, dict) else bool(row)


def set_studio_enabled(machine: str, studio_id: str, enabled: bool):
    """Persist an app-specific scheduler toggle without changing its process."""
    global _flags_cache
    flags = dict(load_flags())
    saved_machine = flags.get(machine, {})
    machine_flags = dict(saved_machine) if isinstance(saved_machine, dict) else {}
    studios = dict(machine_flags.get("studios", {}))
    saved_studio = studios.get(studio_id, {})
    studio_flags = dict(saved_studio) if isinstance(saved_studio, dict) else {}
    studio_flags["enabled"] = bool(enabled)
    studios[studio_id] = studio_flags
    machine_flags["studios"] = studios
    flags[machine] = machine_flags
    FLAGS_FILE.write_text(json.dumps(flags, indent=2) + "\n")
    _flags_cache = flags


def studio_removed(machine: str, studio_id: str) -> bool:
    """Whether full removal explicitly retired a default local registration."""
    machine_flags = load_flags().get(machine, {})
    studio_flags = (
        machine_flags.get("studios", {})
        if isinstance(machine_flags, dict) else {}
    )
    row = studio_flags.get(studio_id, {}) if isinstance(studio_flags, dict) else {}
    return bool(row.get("removed", False)) if isinstance(row, dict) else False


def studio_removal_complete(machine: str, studio_id: str) -> bool:
    """Whether cleanup finished after the durable removal intent was written."""
    machine_flags = load_flags().get(machine, {})
    studio_flags = (
        machine_flags.get("studios", {})
        if isinstance(machine_flags, dict) else {}
    )
    row = studio_flags.get(studio_id, {}) if isinstance(studio_flags, dict) else {}
    return bool(row.get("removal_complete", False)) if isinstance(row, dict) else False


def set_studio_removed(machine: str, studio_id: str, removed: bool):
    """Persist an explicit full-removal marker without losing routing state."""
    global _flags_cache
    flags = dict(load_flags())
    saved_machine = flags.get(machine, {})
    machine_flags = dict(saved_machine) if isinstance(saved_machine, dict) else {}
    studios = dict(machine_flags.get("studios", {}))
    saved_studio = studios.get(studio_id, {})
    studio_flags = dict(saved_studio) if isinstance(saved_studio, dict) else {}
    if removed:
        studio_flags["removed"] = True
    else:
        studio_flags.pop("removed", None)
        studio_flags.pop("removal_complete", None)
    studios[studio_id] = studio_flags
    machine_flags["studios"] = studios
    flags[machine] = machine_flags
    FLAGS_FILE.write_text(json.dumps(flags, indent=2) + "\n")
    _flags_cache = flags


def set_studio_removal_complete(machine: str, studio_id: str, complete: bool):
    """Persist verified completion separately from pre-cleanup removal intent."""
    global _flags_cache
    flags = dict(load_flags())
    saved_machine = flags.get(machine, {})
    machine_flags = dict(saved_machine) if isinstance(saved_machine, dict) else {}
    studios = dict(machine_flags.get("studios", {}))
    saved_studio = studios.get(studio_id, {})
    studio_flags = dict(saved_studio) if isinstance(saved_studio, dict) else {}
    if complete:
        studio_flags["removal_complete"] = True
    else:
        studio_flags.pop("removal_complete", None)
    studios[studio_id] = studio_flags
    machine_flags["studios"] = studios
    flags[machine] = machine_flags
    FLAGS_FILE.write_text(json.dumps(flags, indent=2) + "\n")
    _flags_cache = flags


def load_labels() -> dict:
    global _labels_cache
    if _labels_cache is None:
        try:
            _labels_cache = json.loads(LABELS_FILE.read_text()) if LABELS_FILE.exists() else {}
        except (json.JSONDecodeError, OSError):
            _labels_cache = {}
    return _labels_cache


def set_label(machine: str, name: str):
    global _labels_cache
    labels = dict(load_labels())
    if name and name.strip():
        labels[machine] = name.strip()
    else:
        labels.pop(machine, None)  # empty name clears the alias
    LABELS_FILE.write_text(json.dumps(labels, indent=2) + "\n")
    _labels_cache = labels


def label_for(machine: str) -> str:
    return load_labels().get(machine, machine)


def prune_machine_metadata(known_machines: set[str]) -> None:
    """Remove aliases/toggles whose machine is no longer registered."""
    global _labels_cache, _flags_cache
    labels = {key: value for key, value in load_labels().items()
              if key in known_machines}
    flags = {key: value for key, value in load_flags().items()
             if key in known_machines}
    if labels != load_labels():
        LABELS_FILE.write_text(json.dumps(labels, indent=2) + "\n")
    if flags != load_flags():
        FLAGS_FILE.write_text(json.dumps(flags, indent=2) + "\n")
    _labels_cache = labels
    _flags_cache = flags

# The modalities a legacy (non-packaged) `studios.json` entry may register. Derived from the
# Hub's one routing vocabulary instead of a fourth copy of it: `chat` was missing from the copy
# that used to live here, so a user's `studios.json` entry for the shipped Chat worker was
# silently skipped by `load_registry` (`if modality not in TRACKED_MODALITIES: continue`) - a
# studio that vanished with no message anywhere. The packaged Hub gets its complete local
# registry from its owner-only worker manifest; this set is only the legacy registry's own
# admission rule, and a modality the Hub routes is a modality it may hold.
TRACKED_MODALITIES = frozenset(NODE_MODALITIES)

# The tracked sibling studios and their fixed family ports. `app` is the Pinokio
# launcher folder name under PINOKIO_HOME/api — used by lifecycle control
# (pterm --ref). Override in studios.json if your folder names differ.
DEFAULT_STUDIOS = [
    {"id": "image", "title": "Image Studio KH", "modality": "image",
     "host": "127.0.0.1", "port": 47868, "machine": "local", "emoji": "🎨",
     "app": "imagestudio-mac"},
    {"id": "voice", "title": "Voice Studio KH", "modality": "voice",
     "host": "127.0.0.1", "port": 47870, "machine": "local", "emoji": "🎙️",
     "app": "voicestudio-mac.git"},
]


def load_registry() -> list[dict]:
    """Defaults merged with the optional per-machine studios.json."""
    if _os.environ.get("STUDIOHUB_PACKAGED") == "1":
        from ..standalone_config import load_workers
        from . import fleet_nodes
        # This Mac's own workers, plus one entry per studio on every joined node. A joined
        # node's entry carries its own machine identity, which is what routes a dispatch to it
        # over the peer path instead of to a local worker.
        return load_workers() + fleet_nodes.remote_studios()
    studios = {s["id"]: dict(s) for s in DEFAULT_STUDIOS}
    if REGISTRY_FILE.exists():
        try:
            user_entries = json.loads(REGISTRY_FILE.read_text())
            for entry in user_entries:
                sid = entry.get("id")
                if not sid:
                    continue
                modality = entry.get("modality") or str(sid).split("@", 1)[0]
                if modality not in TRACKED_MODALITIES:
                    continue
                if sid in studios:
                    studios[sid].update(entry)  # override defaults by id
                else:
                    # New entry (e.g. a remote machine's studio). Sensible fill-ins.
                    entry.setdefault("title", sid)
                    entry.setdefault("modality", "unknown")
                    entry.setdefault("host", "127.0.0.1")
                    entry.setdefault("machine", "local")
                    entry.setdefault("emoji", "🧩")
                    if "port" not in entry:
                        continue  # unroutable without a port — skip
                    studios[sid] = entry
        except (json.JSONDecodeError, OSError):
            pass  # malformed override file → fall back to defaults, never crash
    default_ids = {studio["id"] for studio in DEFAULT_STUDIOS}

    def local_checkout_present(studio: dict) -> bool:
        app = studio.get("app")
        if not app:
            return False
        names = [app, app[:-4] if app.endswith(".git") else f"{app}.git"]
        return any((LAUNCHER_ROOT.parent / name).is_dir() for name in dict.fromkeys(names))

    return [studio for studio in studios.values()
            if not (studio.get("id") in default_ids
                    and studio.get("machine", "local") == "local"
                    and studio_removed("local", studio["id"])
                    and not local_checkout_present(studio))]


def base_url(studio: dict) -> str:
    return f"http://{studio['host']}:{studio['port']}"


class RepairRegistryAmbiguity(ValueError):
    """A stable, credential-free reason a registry target cannot be repaired."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RepairMachineSnapshot:
    """Read-only identity evidence for one registered remote machine."""

    machine: str
    registry_host: str
    resolved_address: str
    endpoint_ids: tuple[str, ...]


def _repair_origin_for_host(host: str) -> str:
    candidate = str(host or "")
    if not candidate or candidate != candidate.strip():
        raise RepairRegistryAmbiguity("host_invalid")
    # Registry hosts are hostnames/IP literals, not URL fragments.  The transport
    # validator owns normalization and private-address enforcement.
    display = f"[{candidate}]" if ":" in candidate and not candidate.startswith("[") else candidate
    return f"http://{display}:47873"


def _private_origin_resolver():
    """The private-origin resolver this project does not have, or ``None``.

    Nothing in this tree defines ``resolve_private_origin`` or ``OriginInvalid``. This function
    exists so that absence is a **stated fact** rather than an exception discovered at runtime:
    the identity check below used to start with
    ``from .enrollment_repair_transport import OriginInvalid, resolve_private_origin`` — a module
    that does not exist and never has — so the path always refused, and it refused with a
    ``ModuleNotFoundError`` naming a file nobody could find.

    That dead import was found twice: once by reading, and again by
    ``packaging/stage_voice.staged_import_gaps``, which refuses to build a bundle containing an
    import that resolves to nothing. It is deleted rather than exempted, because a build that
    tolerates a pointer to a missing file tolerates the next one too.

    To implement the resolver: return ``(resolve_private_origin, OriginInvalid)`` from here, where
    ``resolve_private_origin(host, resolver=...)`` returns an object with an ``.address`` and
    ``OriginInvalid`` is raised for a host it cannot resolve. The check below is already written
    against exactly that shape.
    """
    return None


def repair_machine_snapshot(
    rows: Sequence[dict[str, Any]],
    machine: str,
    *,
    resolver: Callable[..., Any] = socket.getaddrinfo,
    expected_address: str | None = None,
) -> RepairMachineSnapshot:
    """Return exact remote identity evidence without probing or changing registry state.

    The registry's ``machine`` field is the identity authority. Labels, profiles,
    scheduler flags, endpoint titles, and inference from a hostname are all
    deliberately excluded from this decision.
    """
    stable_machine = str(machine or "").strip()
    if not stable_machine:
        raise RepairRegistryAmbiguity("machine_missing")
    if stable_machine == "local":
        raise RepairRegistryAmbiguity("machine_local")

    matching = [row for row in rows if str(row.get("machine", "")).strip() == stable_machine]
    if not matching:
        raise RepairRegistryAmbiguity("machine_missing")
    hosts = {str(row.get("host", "")) for row in matching}
    if "" in hosts:
        raise RepairRegistryAmbiguity("host_missing")
    if len(hosts) != 1:
        raise RepairRegistryAmbiguity("machine_multi_host")
    host = next(iter(hosts))

    remote_by_host: dict[str, set[str]] = {}
    for row in rows:
        row_machine = str(row.get("machine", "")).strip()
        row_host = str(row.get("host", ""))
        if row_machine and row_machine != "local" and row_host:
            remote_by_host.setdefault(row_host, set()).add(row_machine)
    if len(remote_by_host.get(host, set())) != 1:
        raise RepairRegistryAmbiguity("host_shared")

    endpoint_ids = []
    for row in matching:
        endpoint_id = str(row.get("id", "")).strip()
        if not endpoint_id:
            raise RepairRegistryAmbiguity("endpoint_missing")
        if "@" in endpoint_id and endpoint_id.rsplit("@", 1)[1] != stable_machine:
            raise RepairRegistryAmbiguity("endpoint_machine_conflict")
        endpoint_ids.append(endpoint_id)
    if len(set(endpoint_ids)) != len(endpoint_ids):
        raise RepairRegistryAmbiguity("endpoint_duplicate")

    # Whether two hostnames are one machine cannot be answered without the resolver above. Until
    # it exists this refuses with the reason it already has for "cannot tell whether these are the
    # same machine" — the same answer the failing import gave, said in the words of the thing that
    # is missing instead of naming a file nobody can find.
    delivered = _private_origin_resolver()
    if delivered is None:
        raise RepairRegistryAmbiguity("host_address_ambiguous")
    resolve_private_origin, OriginInvalid = delivered
    try:
        origin = resolve_private_origin(_repair_origin_for_host(host), resolver=resolver)
    except (OriginInvalid, OSError, TypeError, ValueError):
        raise RepairRegistryAmbiguity("host_address_ambiguous") from None
    # A different hostname is not a different machine identity when it pins to
    # the same direct private address.  Resolve only registry hosts; this is
    # identity validation, never a peer/service probe.
    for other_host in remote_by_host:
        if other_host == host:
            continue
        try:
            other = resolve_private_origin(
                _repair_origin_for_host(other_host), resolver=resolver,
            )
        except (RepairRegistryAmbiguity, OriginInvalid, OSError, TypeError, ValueError):
            continue
        if other.address == origin.address:
            raise RepairRegistryAmbiguity("address_shared")
    if expected_address is not None and origin.address != str(expected_address):
        raise RepairRegistryAmbiguity("host_address_changed")
    return RepairMachineSnapshot(
        machine=stable_machine,
        registry_host=host,
        resolved_address=origin.address,
        endpoint_ids=tuple(sorted(endpoint_ids)),
    )


def remove_machine(machine: str) -> int:
    """Drop a machine's registry entries and machine-specific UI settings."""
    global _labels_cache, _flags_cache
    if not REGISTRY_FILE.exists():
        return 0
    try:
        existing = json.loads(REGISTRY_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return 0
    kept = [e for e in existing if e.get("machine") != machine]
    removed = len(existing) - len(kept)
    if removed:
        REGISTRY_FILE.write_text(json.dumps(kept, indent=2) + "\n")
        labels = dict(load_labels())
        if labels.pop(machine, None) is not None:
            LABELS_FILE.write_text(json.dumps(labels, indent=2) + "\n")
        _labels_cache = labels
        flags = dict(load_flags())
        if flags.pop(machine, None) is not None:
            FLAGS_FILE.write_text(json.dumps(flags, indent=2) + "\n")
        _flags_cache = flags
        from .hardware_profiles import remove_machine_hardware_profile
        remove_machine_hardware_profile(machine)
    return removed


def remove_studio(studio_id: str) -> int:
    """Drop a single studios.json entry by id (e.g. 'music@macmini-m1-01') — for
    pruning a studio type that isn't installed on that machine, without removing
    the rest. Local defaults live in code, not the file, so they're untouched."""
    global _flags_cache
    if not REGISTRY_FILE.exists():
        return 0
    try:
        existing = json.loads(REGISTRY_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return 0
    kept = [e for e in existing if e.get("id") != studio_id]
    removed = len(existing) - len(kept)
    if removed:
        REGISTRY_FILE.write_text(json.dumps(kept, indent=2) + "\n")
        entry = next((e for e in existing if e.get("id") == studio_id), None)
        if entry:
            machine = entry.get("machine", "local")
            flags = dict(load_flags())
            saved_machine = flags.get(machine, {})
            machine_flags = (
                dict(saved_machine) if isinstance(saved_machine, dict) else {}
            )
            studios = dict(machine_flags.get("studios", {}))
            if studios.pop(studio_id, None) is not None:
                machine_flags["studios"] = studios
                flags[machine] = machine_flags
                FLAGS_FILE.write_text(json.dumps(flags, indent=2) + "\n")
                _flags_cache = flags
    return removed


def add_user_entries(entries: list[dict]) -> int:
    """Append/merge entries into studios.json (per-machine registry state)."""
    existing = []
    if REGISTRY_FILE.exists():
        try:
            existing = json.loads(REGISTRY_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            existing = []
    by_id = {e.get("id"): e for e in existing if e.get("id")}
    endpoints = {(e.get("host"), e.get("port")) for e in existing
                 if e.get("host") and e.get("port")}
    added = 0
    for entry in entries:
        endpoint = (entry.get("host"), entry.get("port"))
        duplicate_endpoint = all(endpoint) and endpoint in endpoints
        studio_id = entry.get("id")
        if studio_id in by_id:
            old_endpoint = (by_id[studio_id].get("host"),
                            by_id[studio_id].get("port"))
            if duplicate_endpoint and endpoint != old_endpoint:
                continue
            by_id[studio_id] = {**by_id[studio_id], **entry}
            endpoints.discard(old_endpoint)
            if all(endpoint):
                endpoints.add(endpoint)
        elif studio_id and not duplicate_endpoint:
            by_id[studio_id] = entry
            if all(endpoint):
                endpoints.add(endpoint)
            added += 1
    REGISTRY_FILE.write_text(json.dumps(list(by_id.values()), indent=2) + "\n")
    return added
