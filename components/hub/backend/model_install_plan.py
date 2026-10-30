"""The install-time model picker's answer: what this Mac can install, from where, and what it costs.

**Owner decision 2026-10-28.** A new Mac is set up on one screen that shows this Mac's memory, the
free space on the *model library's* volume, four presets sized to memory, and every other model
grouped by the job it does. Before anything is copied the screen says how much is coming
("62 GB to copy, 380 GB free after"), and it refuses when the disk would fall below the floor the
Hub already stops work at (``disk_governor.STOP_BYTES``).

Four rules this module holds, each of them a defect this project has already paid for:

* **The free space is the library's, not the data directory's.** ``/api/hub/resources`` reports the
  free space of the data dir, and the owner's models live on an external drive in every kit setup,
  so that number has been answering a question nobody asked. Here the volume is the library's.
* **A model missing from the SSD is not a missing model.** The kit's 2026-10-28 write shipped
  manifests without weights for ``florence-2-large-ft`` and ``phonon-2-mlx``. A preset that named
  an unshipped model must fall back to another Mac on the LAN or to the pinned internet URLs *for
  that model only*, say which source it will use, and keep it in the totals.
* **A preset is never "complete" until every package is installed and verified.** The count is
  built from the receipts (``model_packages.preflight``), never from the sizes, never from
  ``progress = 100``.
* **Everything is computed on the way out and nothing is remembered.** A projection cached past a
  model switch, a disk change or an install is a number the screen shows that is no longer true.

Read-only: this module reads manifests, receipts, the disk and the kit folders **it is given**. It
never copies, moves or deletes anything - the screen's Apply is the existing
``model-packages/{id}/download`` route, one request per package.

The Hub reads its chosen model library, never a mounted kit. SSD copying is started by the person
using the kit's Terminal command.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from components import model_presets

#: One-line purpose and job group per package, for the manual picker. **A model with no entry here
#: still appears** - it is filed under the job its engine serves and its purpose line is its
#: manifest's own ``display_name`` - because a per-model list in a consumer is the defect the
#: standing rule forbids ("a new model must not need a code change"). This table only improves the
#: words for the models it knows.
PURPOSES: dict[str, str] = {
    "z-image-turbo-mflux-4bit": "Fast pictures from words; the everyday image model on a small Mac.",
    "flux2-klein-4b-mlx-4bit": "Pictures, plus editing a picture with a reference.",
    "qwen-image-2-1-mflux-q4": "Higher-fidelity pictures when the Mac has the memory.",
    "qwen-image-2-1-turbo-mflux-q4": "Higher-fidelity pictures in eight steps.",
    "qwen-image-2-1-mlx-serve-4bit": "Qwen Image 2.1 through its native MLX server; 24 GB and up.",
    "qwen3-tts-12hz-0-6b-base-4bit": "Narration that clones a voice from a short clip.",
    "qwen3-tts-12hz-0-6b-base-8bit": "The same narration at 8-bit precision.",
    "qwen3-tts-12hz-1-7b-base-4bit": "The larger narration model: steadier on long scripts.",
    "voxcpm2-4bit": "Expressive narration, cloned or described in words.",
    "voxcpm2-8bit": "Expressive narration at 8-bit precision.",
    "voxcpm2-bf16": "Expressive narration at full precision.",
    "omnivoice-bfloat16": "A second voice family for a second opinion on a line.",
    "breeze-tts-2-mlx-4bit": "A 16 GB-tier voice with a long-form section policy.",
    "fish-audio-s2-pro-4bit": "Natural, expressive narration at 4-bit.",
    "fish-audio-s2-pro-8bit": "Natural, expressive narration at 8-bit.",
    "fish-audio-s2-pro-bf16": "Natural, expressive narration at full precision.",
    "chatterbox-4bit": "An alternative voice family at 4-bit.",
    "chatterbox-8bit": "An alternative voice family at 8-bit.",
    "pocket-tts-4bit": "A small voice model for very small Macs.",
    "khmer-tts-mms": "Khmer narration (MMS VITS).",
    "khmer-tts-female-onnx": "Khmer narration, female voice (VITS/ONNX).",
    "whisper-turbo": "Transcribes audio and video; the default subtitle model.",
    "whisper-base": "A smaller transcription model, faster and less accurate.",
    "whisper-small": "A middle transcription model.",
    "phonon-2-mlx": "An alternative transcription model.",
    "qwen3-5-9b-4bit": "Writes scripts and prose; the 16 GB writing model.",
    "gemma-3-12b-it-4bit": "Writes longer text; the 24 GB writing model.",
    "qwen36-35b-4bit": "The largest writing model; 32 GB and up.",
    "gemma4-26b-a4b-qat-4bit": "Gemma 4 26B for local testing on a 32 GB Mac.",
    "nemotron-lightning-4bit": "A 30B writing model with sparse activation; 24 GB and up.",
    "florence-2-base-ft": "Reads a picture and captions it.",
    "florence-2-large-ft": "Reads a picture with more detail.",
    "depth-anything-v2-small": "Measures depth in a picture (scene planning).",
    "stable-audio-3-medium": "Generates music and sound from a description.",
    "yue2-3b-mlx": "Generates songs with vocals.",
    "bge-small-en-v1-5-bf16": "Turns text into vectors for the meaning check.",
}

#: Job groups, in the order the screen lists them. Derived from the engine's modality, so a new
#: model lands in a group without an edit; an engine whose modality is not here falls into "other".
JOB_ORDER = ("image", "voice", "writing", "vision", "music", "transcription", "embeddings", "video", "other")

#: Download sources reported by the Hub; SSD copying is a separate person-run command.
SOURCE_LAN = "lan"
SOURCE_INTERNET = "internet"


def library_bytes(manifest: dict) -> int:
    """The bytes this package puts on disk: its files plus its companions', each counted once."""
    return sum(int(entry.get("size") or 0) for entry in manifest.get("files") or [])


#: The Hub's modality -> the job heading the picker groups it under. Only the words differ: an
#: engine's modality comes from the Hub's own routing table (`standalone._ENGINE_ROUTE`), read here
#: through one function, so adding an engine never needs an edit in this file.
JOB_FOR_MODALITY = {"image": "image", "voice": "voice", "chat": "writing", "vision": "vision",
                    "music": "music", "transcription": "transcription", "embeddings": "embeddings",
                    "video": "video"}


def job_for(engine: str, modality_of) -> str:
    """The job heading for an engine, resolved by the Hub's own `_modality_for_engine`.

    `modality_of` is that function, passed in by the route rather than imported: `standalone`
    builds its app at import time, and a module-level import of it here would either be a cycle or
    a second copy of the engine table - the second list the standing rule forbids. An unregistered
    engine is "other" rather than an exception, because its row already carries the manifest error
    and the screen still has to draw.
    """
    try:
        return JOB_FOR_MODALITY.get(modality_of(engine), "other")
    except (ValueError, KeyError):
        return "other"


def dependency_ids(manifests: dict[str, dict], presets: list[dict] | None = None) -> set[str]:
    """Packages that exist only because another one needs them (a tokenizer, a codec, a VAE).

    Read through `model_packages.required_packages` - the app's one reader of that relationship -
    exactly as `/api/hub/model-packages` decides the `type` field, so the picker cannot offer a
    codec as something a person would choose while the catalogue calls it a dependency.

    **A package a preset names is not hidden, even when something else also requires it.**
    `whisper-turbo` is both the transcription model the 8 GB preset lists and a companion of the
    Qwen voice packages. Reading the companion relationship alone would delete it from the screen -
    a real model, chosen by name, hidden because another package happens to need it. Its presence
    in a preset file is the evidence that somebody chooses it.
    """
    from components import model_packages
    chosen = {package for preset in (presets or []) for package in preset.get("packages") or []}
    needed: set[str] = set()
    for manifest in manifests.values():
        if manifest.get("_manifest_error"):
            continue
        try:
            needed.update(model_packages.required_packages(manifest))
        except ValueError:
            continue
    return needed - chosen


def library_volume(library: Path) -> Path:
    """The existing folder whose filesystem the library is on, for a free-space reading.

    A library that has not been created yet resolves to its nearest existing parent, because
    `shutil.disk_usage` on a missing path raises - and "cannot read the disk" must not read as
    "no space".
    """
    candidate = Path(library).expanduser()
    while not candidate.exists():
        if candidate == candidate.parent:
            break
        candidate = candidate.parent
    return candidate


def disk(library: Path) -> dict:
    """Free/total bytes **on the model library's own volume**, or honest nulls.

    Deliberately not ``/api/hub/resources``' figure: that one is the data directory's, and on every
    kit setup the models are on an external drive, so it answered about a different disk.
    """
    try:
        usage = shutil.disk_usage(library_volume(library))
    except OSError:
        return {"path": str(library), "free_bytes": None, "total_bytes": None, "measured": False}
    return {"path": str(library), "free_bytes": usage.free, "total_bytes": usage.total,
            "measured": True}


def _installed(receipts: dict[str, dict], package_id: str) -> bool:
    """True only when the receipt says ``files_verified``.

    ``not_installed``, ``dependency_missing``, ``invalid`` and a missing receipt are all "not
    installed" - and the difference between them is carried in the row's own ``reason``, never
    collapsed into a zero.
    """
    receipt = receipts.get(package_id) or {}
    return receipt.get("state") == "files_verified"


def model_rows(*, manifests: dict[str, dict], receipts: dict[str, dict], memory_gb: float | None,
               modality_of, presets: list[dict] | None = None,
               lan_source: dict[str, str] | None = None) -> list[dict]:
    """One row per package: what it is, what it costs, whether it is here, and how it would arrive.

    A dependency (a tokenizer, a codec) is **not** a row: nobody chooses it, and it arrives with the
    package that needs it, which is why a preset never lists one either.
    """
    companions = dependency_ids(manifests, presets)
    rows: list[dict] = []
    for package_id in sorted(manifests):
        manifest = manifests[package_id]
        if manifest.get("_manifest_error") or package_id in companions:
            continue
        engine = str(manifest.get("engine") or "")
        rows.append({
            "package_id": package_id,
            "model_id": manifest.get("model_id"),
            "label": (manifest.get("display_name") or _label(package_id)),
            "engine": engine,
            "job": job_for(engine, modality_of),
            "purpose": PURPOSES.get(package_id) or (manifest.get("display_name") or _label(package_id)),
            "quantization": manifest.get("quantization"),
            "bytes": library_bytes(manifest),
            "floor_gb": model_presets.floor_gb(package_id, manifest),
            "installed": _installed(receipts, package_id),
            "state": (receipts.get(package_id) or {}).get("state") or "not_installed",
            "reason": _refusal(package_id, manifest, memory_gb),
            "sources": _sources(package_id, manifest, lan_source),
        })
    return rows


def _label(package_id: str) -> str:
    """A readable name derived from the id, never a made-up product name."""
    return package_id.replace("-mlx-4bit", "").replace("-mflux-q4", "").replace("-4bit", "").replace("-", " ").title()


def _refusal(package_id: str, manifest: dict, memory_gb: float | None) -> str | None:
    """Why this model cannot run here, or None. Naming both numbers is the owner's rule."""
    return model_presets.refusal(package_id, manifest, memory_gb)


def _sources(package_id: str, manifest: dict,
             lan_source: dict[str, str] | None) -> list[str]:
    """Where this package's bytes could come from, best first, **as it stands right now**.

    The downloader tries another Mac's LAN share before the pinned internet URLs.
    """
    found: list[str] = []
    if lan_source and package_id in lan_source:
        found.append(SOURCE_LAN)
    if any(entry.get("urls") for entry in manifest.get("files") or []):
        found.append(SOURCE_INTERNET)
    return found


def projection(*, packages: list[str], rows: dict[str, dict], disk_state: dict,
               stop_bytes: int) -> dict:
    """What copying these packages means, before any byte is copied.

    ``to_copy_bytes`` counts only what is **not already installed and verified** - the number the
    screen promises, so a second run of the same preset costs what is left rather than the whole
    set again. ``free_after_bytes`` is this Mac's library volume minus that, and ``refused`` says
    whether the result would fall below the floor the Hub already stops work at.

    ``refused`` is a sentence naming both numbers, or None: "the answer is none" and "I could not
    work this out" must not produce the same value, so an unmeasured disk is ``refused: None`` with
    ``disk.measured: false`` beside it and the screen says it does not know rather than showing a
    number that is true by accident.
    """
    to_copy = 0
    missing: list[str] = []
    for package_id in packages:
        row = rows.get(package_id)
        if row is None:
            missing.append(package_id)
            continue
        if not row["installed"]:
            to_copy += int(row["bytes"])
    free = disk_state.get("free_bytes")
    free_after = None if free is None else int(free) - to_copy
    refusal = None
    if free_after is not None and free_after < stop_bytes:
        refusal = (f"Not enough free space on {disk_state.get('path')}: copying {_gb(to_copy)} would leave "
                   f"{_gb(free_after)}, and this Mac keeps {_gb(stop_bytes)} free for working room. "
                   f"Free some space, or choose a model library on a larger disk.")
    return {"to_copy_bytes": to_copy, "free_after_bytes": free_after,
            "needed_bytes": to_copy + stop_bytes, "stop_bytes": stop_bytes,
            "refused": refusal, "unknown_packages": missing}


def _gb(value: int | float | None) -> str:
    if value is None:
        return "an unknown amount"
    return f"{float(value) / 1e9:.1f} GB"


def preset_rows(*, presets: list[dict], rows: dict[str, dict], manifests: dict[str, dict],
                memory_gb: float | None, disk_state: dict, stop_bytes: int) -> list[dict]:
    """Every preset with its own package list, totals and completion count.

    **Complete means installed and verified, every one of them.** The count is built from the
    receipts, so a preset whose models are 90% on the disk is not "almost done" - it is 9 of 10 -
    and a model the SSD never shipped still counts as missing until it has really arrived.
    """
    out = []
    for preset in presets:
        packages = model_presets.install_set(preset, manifests)
        listed = [package_id for package_id in packages if package_id in rows]
        installed = [package_id for package_id in listed if rows[package_id]["installed"]]
        chosen = projection(packages=listed, rows=rows, disk_state=disk_state, stop_bytes=stop_bytes)
        out.append({
            "preset_id": preset["preset_id"],
            "name": preset["name"],
            "memory_tier_gb": preset["memory_tier_gb"],
            "summary": preset.get("summary"),
            "packages": listed,
            "own_packages": list(preset["packages"]),
            "installed_count": len(installed),
            "package_count": len(listed),
            # A preset is complete only when every package is installed and verified.
            "complete": len(listed) > 0 and len(installed) == len(listed),
            "fits_this_mac": memory_gb is not None and float(memory_gb) >= preset["memory_tier_gb"],
            "projection": chosen,
            "refused_models": [row["package_id"] for row in (rows[p] for p in listed) if row["reason"]],
        })
    return out


def snapshot(*, manifests: dict[str, dict], receipts: dict[str, dict],
             library: Path, memory_gb: float | None, stop_bytes: int, modality_of,
             lan_source: dict[str, str] | None = None) -> dict:
    """The whole screen: this Mac, four presets, and every model grouped by its job.

    One read, computed fresh: a cached projection is a number the screen shows that a model
    switch, an install or a disk change has already made wrong.
    """
    disk_state = disk(library)
    shipped = model_presets.load()
    rows = model_rows(manifests=manifests, receipts=receipts, memory_gb=memory_gb,
                      modality_of=modality_of, presets=shipped, lan_source=lan_source)
    by_id = {row["package_id"]: row for row in rows}
    presets = preset_rows(presets=shipped, rows=by_id, manifests=manifests,
                          memory_gb=memory_gb, disk_state=disk_state, stop_bytes=stop_bytes)
    chosen = model_presets.starting_id(memory_gb)
    groups = []
    for job in JOB_ORDER:
        members = sorted((row for row in rows if row["job"] == job),
                         key=lambda row: (row["floor_gb"] or 0, row["label"].lower()))
        if members:
            groups.append({"job": job, "models": members})
    return {
        "memory_gb": memory_gb,
        "disk": disk_state,
        # The folder the free figure belongs to, so the screen can name the drive.
        "library": str(library),
        "stop_bytes": stop_bytes,
        "presets": presets,
        "starting_preset": chosen,
        "groups": groups,
        "models": rows,
        "problems": _preset_problems(manifests),
    }


def _preset_problems(manifests: dict[str, dict]) -> list[str]:
    """Every preset id with no manifest, so a typo is visible on the screen rather than silent."""
    found: list[str] = []
    for preset in model_presets.load():
        found.extend(model_presets.problems(preset, manifests))
    return found
