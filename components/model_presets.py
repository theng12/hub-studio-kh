"""The four install presets, read from ``model-presets/`` as data.

**Owner decision 2026-10-28.** A new Mac is set up by choosing one of four presets sized to its
memory, and every model outside that preset is chosen by hand. Nothing about these lists lives in
code: adding a model to a preset, renaming a preset or adding a fifth is an edit to a JSON file,
the same rule ``retired_models.py`` and the manifests follow ("a new model must not need a code
change").

Three rules this module holds, and the reasons, because each one is a defect that has already
happened here:

* **RAM is the only gate.** A model is never hidden or refused by name; a model whose floor is
  above this Mac is returned with the reason so the screen can grey it out. ``eligible`` here is
  about the preset's own tier, never a judgement about the model.
* **A preset includes the smaller ones.** Each preset file lists its own additions *and* every
  package the smaller presets carry, so a reader never has to union four files to know what a Mac
  gets. ``tests/test_model_presets.py`` pins that superset property, so a file that drops a
  smaller preset's package fails loudly instead of quietly shipping a thinner 32 GB Mac.
* **A companion is never listed.** ``requires_packages`` already says that ``yue2-3b-mlx`` is no
  use without ``yue2-vae``; listing a tokenizer or a codec here would make it look like something
  a person chose. ``install_set`` resolves companions through the app's own reader
  (``model_packages.required_packages``), exactly as the downloader does.

Read-only: this module reads the preset files and the manifests it is handed. It never writes,
moves or deletes anything.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

#: The data directory, shipped beside ``model-manifests/``.
PRESET_FOLDER = "model-presets"

#: The preset a Mac that has chosen nothing is offered, by the tier it falls in. **A starting
#: value only**: the screen shows every preset this Mac can run and the owner may pick another.
#: Four tiers, and the last one is open-ended (32 GB and above), so a 64 GB Mac is not left with
#: no preset at all.
TIER_TO_PRESET = ((8, "essentials-8"), (16, "creator-16"), (24, "creator-plus-24"), (32, "studio-pro-32"))


class PresetError(ValueError):
    """A preset file this build cannot use, naming the file and the field to fix."""


def preset_root() -> Path:
    """Where the preset files are.

    ``STUDIOHUB_MODEL_PRESETS`` is set by the packaged Hub's launcher the same way
    ``STUDIOHUB_MODEL_MANIFESTS`` is; otherwise the folder beside this checkout's ``components/``.
    The data directory is deliberately *shared* with the manifests' rule, so a test that points one
    at a fixture points both at the same idea of the catalog.
    """
    location = os.environ.get("STUDIOHUB_MODEL_PRESETS", "").strip()
    if location:
        return Path(location).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / PRESET_FOLDER


def _require(condition: bool, preset_id: str, message: str) -> None:
    if not condition:
        raise PresetError(f"model-presets/{preset_id}.json {message}")


def validate(preset: object, stem: str | None = None) -> dict:
    """One preset, or ``PresetError`` naming the field. The same shape the screen renders."""
    if not isinstance(preset, dict):
        raise PresetError(f"model-presets/{stem or '?'}.json is not a JSON object")
    preset_id = str(preset.get("preset_id") or stem or "")
    _require(preset.get("schema_version") == 1, preset_id, "has an unsupported schema_version")
    _require(bool(preset_id), preset_id, "has no preset_id")
    _require(bool(str(preset.get("name") or "").strip()), preset_id, "has no name")
    # The owner's rule: every preset name carries its RAM number, so a screen that shows only the
    # name still tells the operator what it is for. A name without a number is a defect here
    # rather than a style note, because the number is the whole choice.
    _require(any(ch.isdigit() for ch in preset["name"]), preset_id,
             f"has the name {preset['name']!r}, which carries no RAM number")
    tier = preset.get("memory_tier_gb")
    _require(isinstance(tier, int) and not isinstance(tier, bool) and tier > 0, preset_id,
             "has no positive integer memory_tier_gb")
    packages = preset.get("packages")
    _require(isinstance(packages, list) and bool(packages), preset_id, "lists no packages")
    for entry in packages:
        _require(isinstance(entry, str) and entry and "/" not in entry and "\\" not in entry,
                 preset_id, f"has a package entry that is not a package id: {entry!r}")
    _require(len(set(packages)) == len(packages), preset_id, "lists the same package twice")
    return preset


def load() -> list[dict]:
    """Every preset, smallest tier first. A file this build cannot use fails loudly.

    **Deliberately not tolerant**, unlike the model catalog: a broken *manifest* degrades one row
    of a list the app draws, while a broken *preset* would silently change what a new Mac
    installs. The whole screen is refused with the file and field named instead.
    """
    root = preset_root()
    try:
        paths = sorted(path for path in root.glob("*.json") if not path.name.startswith("."))
    except OSError as exc:
        raise PresetError(f"model-presets could not be read at {root}: {exc}") from exc
    if not paths:
        raise PresetError(f"no preset files in {root}; the app cannot offer a new Mac a starting set")
    presets = []
    for path in paths:
        try:
            presets.append(validate(json.loads(path.read_text(encoding="utf-8")), path.stem))
        except (OSError, ValueError) as exc:
            if isinstance(exc, PresetError):
                raise
            raise PresetError(f"model-presets/{path.name} could not be read as JSON: {exc}") from None
    presets.sort(key=lambda item: (item["memory_tier_gb"], item["preset_id"]))
    return presets


def by_id(preset_id: str) -> dict:
    for preset in load():
        if preset["preset_id"] == preset_id:
            return preset
    raise PresetError(f"no preset named {preset_id!r}; the presets are "
                      f"{[item['preset_id'] for item in load()]}")


def starting_id(memory_gb: int | float | None) -> str | None:
    """The preset to *offer* a Mac with this much memory, or None when nothing is known.

    Not a decision on the operator's behalf: it is what the screen marks as "nearest" - on a fresh
    Mac and on one that is already set up - and the owner may choose any preset.

    It rounds **down**, so the marked preset is one this Mac can run in full: a 12 GB Mac marks the
    8 GB set, not the 16 GB one whose models its own floor check would refuse. One function, because
    a second "nearest" that could disagree with the offer is the second vocabulary this project
    keeps paying for.

    ``None`` in, ``None`` out - a memory figure the Hub could not read must not silently become
    8 GB and a smaller starting set.
    """
    if not isinstance(memory_gb, (int, float)) or isinstance(memory_gb, bool) or memory_gb <= 0:
        return None
    chosen = None
    for tier, preset_id in TIER_TO_PRESET:
        if float(memory_gb) >= tier:
            chosen = preset_id
    return chosen


def install_set(preset: dict, manifests: dict[str, dict], *, include_companions: bool = True) -> list[str]:
    """The package ids an install of ``preset`` copies: its own list plus the companions it needs.

    A companion is resolved through ``model_packages.required_packages`` - the app's one reader of
    that relationship - so a package that gains a companion later is carried without an edit here.
    A companion with no manifest is skipped rather than invented: the downloader would refuse it
    anyway, and ``problems`` is where that is reported.
    """
    wanted: list[str] = []
    for package_id in preset["packages"]:
        if package_id not in wanted:
            wanted.append(package_id)
    if not include_companions:
        return wanted
    from components import model_packages      # lazy: the kit's copy script imports this file by bare name
    for package_id in list(wanted):
        manifest = manifests.get(package_id)
        if not isinstance(manifest, dict):
            continue
        for dependency in model_packages.required_packages(manifest):
            if dependency not in wanted:
                wanted.append(dependency)
    return wanted


def problems(preset: dict, manifests: dict[str, dict]) -> list[str]:
    """Everything wrong with this preset against the catalog it will install from.

    **Every id must exist as a manifest.** A preset naming a package the catalog does not ship is
    a Mac that reports an install finished while a lane it was promised is missing, which is the
    "verified completion" failure this project treats as a defect rather than a warning. Called by
    the route (so the screen shows it) and by the test (so a typo fails the build).
    """
    found: list[str] = []
    for package_id in preset["packages"]:
        if package_id not in manifests:
            found.append(f"{preset['preset_id']}: no manifest model-manifests/{package_id}.json")
    for package_id in install_set(preset, manifests):
        if package_id not in manifests:
            found.append(f"{preset['preset_id']}: {package_id} is required by another package in the "
                         f"preset but has no manifest")
    return found


def floor_gb(package_id: str, manifest: dict) -> int | None:
    """This model's RAM floor from its own manifest, or None when it states none.

    **One source.** The kit used to carry two hand-kept tables (``PROVEN_SMALLER`` and
    ``CARRY_BELOW_TIER``) so a model the owner had run on a smaller Mac could be carried there;
    both are gone and their numbers live in the manifests' own ``qualification`` block, which is
    where a re-capture preserves them. An absent floor stays absent: "no number published" and
    "needs zero" must not publish the same value.
    """
    qualification = manifest.get("qualification") if isinstance(manifest, dict) else None
    value = (qualification or {}).get("minimum_unified_memory_gb")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return int(round(value))
    return None


def refusal(package_id: str, manifest: dict, memory_gb: int | float | None) -> str | None:
    """Why a model cannot run on this Mac, or None when it can.

    The sentence names **the floor and this Mac's memory**, because the owner's rule is that a
    model too big for the Mac is shown greyed out *with the reason*. A Mac whose memory could not
    be read is not refused: that is a missing measurement, not a small machine.
    """
    floor = floor_gb(package_id, manifest)
    if floor is None or not isinstance(memory_gb, (int, float)) or isinstance(memory_gb, bool):
        return None
    if float(memory_gb) < floor:
        return (f"Needs a Mac with at least {floor} GB of memory; this one has "
                f"{float(memory_gb):g} GB. Everything else installs; this model is left out.")
    return None
