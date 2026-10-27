#!/usr/bin/env python3
"""Copy the models this Mac can run into your Downloads folder, and update them later.

Double-click **copy-models.command** instead of running this directly.

It reads how much memory this Mac has, looks at what each model needs, and copies only the ones
that fit - so a small Mac does not carry 29 GB it can never use.

Running it again is safe and is the normal thing to do after the SSD is refreshed with new
models. It compares each model folder against the copy already here and:

  * copies models this Mac has never had,
  * replaces models whose shipped files changed (a corrected or updated weight file),
  * leaves untouched models that are already identical,
  * keeps the app's record of what is installed in step with the new files, so an updated model
    still reads as installed instead of needing to be re-verified by hand,
  * moves a model this kit no longer offers out of the way, into `_trash` beside the library, so a
    model cut from a release does not sit on the Mac forever.

Nothing is ever deleted. Previous copies, partial copies and old revision folders are moved into a
unique folder under `<library>/_trash`, which is the operator's to empty when he is satisfied
nothing in it is wanted. Moving them there preserves the evidence and does not reclaim space.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Where the library goes. A sibling of the home folder, not ~/Downloads: macOS privacy
# protection covers Downloads, and a service the app starts can be blocked from reading a
# library inside it - the worker then waits inside open() forever and the app never becomes
# ready. Kept here as one name so the app, the kit and this script cannot drift apart.
#
# **The authoritative answer comes from the app now** (owner report, 2026-10-18: the kit filled
# `~/StudioHubModels` while the Hub on that Mac was using a different folder, so it had to be
# repointed by hand). `components/model_library.py`, which ships inside the app, resolves the folder
# the Hub is actually using from its own preferences; these two names are the fallback for an older
# app that does not carry it, and must stay equal to the resolver's.
LIBRARY_FOLDER = "StudioHubModels"
LEGACY_LIBRARY_FOLDER = Path("Downloads") / "model-library"


def shared_resolver():
    """The app's own library resolver, or None on an app that predates it.

    Imported lazily: the app's `components/` folder is only on `sys.path` once the bundle has been
    found in `main()`, exactly as `retired_models` and `shelved_models` are.
    """
    try:
        from model_library import resolve_library  # type: ignore
    except ImportError:
        return None
    return resolve_library

# **No floor table lives here any more (owner decision 2026-10-28, "collapse duplicated floor
# tables into manifests and delete the old tables").** This file used to carry `PROVEN_SMALLER`
# (the owner had run a model on a smaller Mac) and `CARRY_BELOW_TIER` (he had asked to try it
# there, unmeasured). Both contained exactly the numbers their models' own manifests now state:
# FLUX.2 Klein 4-bit and Breeze TTS 2 4-bit moved 16 -> 8 in `model-manifests/` on 2026-10-28,
# carrying the evidence sentence with them, and Z-Image-Turbo 4-bit's manifest already said 8.
# A model's floor is its `qualification.minimum_unified_memory_gb` and nothing else, so a
# re-capture (`packaging/capture_model_manifests.py`) cannot leave a second copy behind to
# disagree with it.
#
# Where the two claims went: a measured run is recorded in the manifest's `memory_evidence` and
# `status`, which is the artefact the capture tool already preserves; there is no longer a place
# to record "carried on his request, still unmeasured" as distinct from "proven", so that
# distinction is made by writing the evidence above the number rather than by choosing a table.

# Offered but not stocked (owner decision 2026-10-08): the model stays on the SSD, in the catalog and
# installable on request (the Gateway's "Install a model..." / `POST /admin/api/fleet/model-installs`),
# but this step does not copy it to a Mac. Unlike a shelved model (`components/shelved_models.py`) the
# Hub neither moves it out nor refuses to download it. A Mac marked keep-all copies it like any other.
NOT_STOCKED = {
    # Qwen Image 2.1 (native mlx-serve 4-bit), qualified on 24 GB; the fleet's stocked Qwen is the
    # low-RAM `qwen-image-2-1-mflux-q4` (16 GB and up).
    "qwen-image-2-1-mlx-serve-4bit",
}


def machine_memory_gb() -> int:
    """Installed memory in whole GiB, the way the machine is sold."""
    if platform.system() == "Darwin":
        try:
            out = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.memsize"],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            return round(int(out) / (1024 ** 3))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 ** 3))


def required_gb(manifest: dict) -> int:
    """This model's memory floor, from its own manifest and nowhere else.

    `0` means the manifest published no floor, which the caller reads as "fits anywhere" - the
    same answer it gave before, and the honest one: an absent figure is a missing measurement, not
    a small requirement.
    """
    qualification = manifest.get("qualification") or {}
    floor = qualification.get("minimum_unified_memory_gb")
    return int(floor) if isinstance(floor, (int, float)) and not isinstance(floor, bool) and floor > 0 else 0


def floor_origin(manifest: dict) -> str:
    """Where this model's memory requirement came from, so a wrong skip can be traced.

    One source now: the manifest's own `qualification.minimum_unified_memory_gb`. The sentence
    names the file to edit, because the skip *is* printed and a number with no origin leaves the
    operator guessing which of several possible places to change. See the comment above for where
    the two former tables went.
    """
    return (f"floor from qualification.minimum_unified_memory_gb in "
            f"model-manifests/{manifest.get('package_id', '?')}.json (raise it there; the evidence "
            f"for the number belongs in that block's memory_evidence)")


def size_gb(manifest: dict) -> float:
    return sum(int(f.get("size") or 0) for f in manifest.get("files", [])) / 1e9


def folder_signature(root: Path):
    """A cheap identity for a model folder: its shape and every file's size.

    Sizes, not hashes: a changed weight file changes size for every case that actually happens
    (a re-exported model, a corrected file, a truncated copy), and hashing 29 GB on every run
    would make the "just run it again" step unbearable. Integrity is proven afterwards by the
    app's own verifier, which hashes every byte exactly once per model it copies.
    """
    rows = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows.append((relative, "link", os.readlink(path)))
        elif path.is_dir():
            rows.append((relative, "dir", 0))
        else:
            try:
                size = path.stat().st_size
            except OSError:
                size = -1
            # A download the app left unfinished is zero bytes and is not part of the model.
            if relative.endswith(".incomplete") and size == 0:
                continue
            rows.append((relative, "file", size))
    return tuple(rows)


def needs_copy(source: Path, target: Path) -> bool:
    """True when the model here is missing or differs from the model on the SSD."""
    if not target.is_dir():
        return True
    return folder_signature(source) != folder_signature(target)


def copy_folder(source: Path, target: Path) -> None:
    """Copy a model folder over the one already here, without ever leaving a half-copy behind.

    The old folder is moved aside first and archived once the new one is in place. A previous
    interrupted run is recovered before another copy starts, and every partial or displaced tree
    is retained under this library's `_trash` instead of being deleted.
    """
    staging = target.with_name(target.name + ".incoming")
    retired = target.with_name(target.name + ".replaced")
    library = target.parent

    # A crash between moving the live folder aside and publishing the incoming one leaves the
    # previous good copy at `.replaced`. Restore that rollback point before touching anything else.
    if not os.path.lexists(target) and os.path.lexists(retired):
        retired.rename(target)
    for path, label in ((staging, f"{target.name}.incoming"),
                        (retired, f"{target.name}.replaced")):
        if os.path.lexists(path):
            move_to_trash(path, library, label)

    result = subprocess.run(["/bin/cp", "-Rp", str(source), str(staging)])
    if result.returncode != 0:
        if os.path.lexists(staging):
            try:
                move_to_trash(staging, library, f"{target.name}.incoming")
            except OSError:
                pass  # leave the partial beside the library when even the preserving move fails
        raise OSError(f"could not copy {source.name}")
    try:
        if os.path.lexists(target):
            target.rename(retired)
        staging.rename(target)
    except OSError:
        if os.path.lexists(retired) and not os.path.lexists(target):
            retired.rename(target)
        if os.path.lexists(staging):
            try:
                move_to_trash(staging, library, f"{target.name}.incoming")
            except OSError:
                pass
        raise
    if os.path.lexists(retired):
        move_to_trash(retired, library, f"{target.name}.replaced")


def prune_old_revisions(target: Path, keep: str) -> int:
    """Archive revision folders of an updated model that the current manifest no longer names.

    A model folder holds one subfolder per revision. Copying a new revision in would otherwise
    leave the previous revision beside the active one. The archive remains under the model library
    until its operator chooses to empty `_trash`.
    """
    archived = 0
    try:
        children = sorted(target.iterdir())
    except OSError:
        return 0
    for child in children:
        if child.is_symlink() or not child.is_dir() or child.name == keep:
            continue
        if len(child.name) != 40 or any(c not in "0123456789abcdef" for c in child.name):
            continue  # not a revision folder: leave anything unfamiliar alone
        size = sum(f.stat().st_size for f in child.rglob("*")
                   if f.is_file() and not f.is_symlink())
        try:
            move_to_trash(child, target.parent, f"{target.name}.revision.{child.name}")
        except OSError:
            continue
        archived += size
    return archived


#: Where a package this kit no longer offers is moved. A move, not a delete: the folder is still
#: the operator's, `_trash` is his to empty, and a model reinstated in a later kit is still there.
TRASH_FOLDER = "_trash"

#: The Hub's device settings, relative to the home folder: where a Mac is marked "keep all models".
HUB_SETTINGS_RELATIVE = Path("Library/Application Support/StudioHub Standalone/hub/device_settings.json")

#: The app's receipt log, relative to the library it belongs to.
INDEX_RELATIVE = Path(".studiohub") / "installed.json"


def read_index(library: Path) -> dict:
    """The app's record of what is installed in this library, or {} when there is none.

    The validity rule is the app's own (`_index` in `components/model_packages.py`): an object,
    every value an object. A record the app itself would refuse to read is not a record this
    script will move folders on the strength of.
    """
    try:
        value = json.loads((library / INDEX_RELATIVE).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict) or any(not isinstance(row, dict) for row in value.values()):
        return {}
    return value


def write_index(library: Path, index: dict) -> None:
    """Replace the receipt log in one step, in the shape the app itself writes it.

    `.tmp` then `os.replace`, so an interrupted run leaves either the old log or the new one - never
    a half-written file. A damaged index reads to the app as "nothing is installed" for every model
    on this Mac, which is a far worse outcome than a model that is still listed after a failed run.
    """
    state = library / ".studiohub"
    state.mkdir(mode=0o700, exist_ok=True)
    temporary = state / (INDEX_RELATIVE.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(json.dumps(index, sort_keys=True))
    os.replace(temporary, state / INDEX_RELATIVE.name)


def folder_bytes(folder: Path) -> int:
    """Every file in a package folder, in bytes - the count the kit's own verifier reports."""
    total = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def plan_prune(index: dict, library: Path,
               offered: set[str]) -> tuple[list[tuple[str, Path, int]], list[str]]:
    """What the library holds that this kit no longer offers, in the two groups it is handled in.

    A managed package is a **key in the app's own record whose folder really is a directory**.
    `installed.json` is the only thing that says a folder belongs to this app, and it is the only
    thing this script may move. The library holds other things on purpose - the Hugging Face cache
    in `hub/`, a candidate model the owner is still qualifying, an older `_trash` - and those are
    his, not the kit's. The folder is tested as the *second* condition, never the first: the record
    is a historical receipt log (the owner's Mac holds 42 receipts and 15 folders), so treating it
    as a picture of the disk would name folders that are not there.

    Returns (packages to move, stale receipts to forget). A stale receipt names a package this kit
    no longer offers whose folder is already gone, so removing it changes nothing on disk - it is
    a line in the log that names a folder nobody will find. A receipt whose package *is* still
    shipped is kept even with no folder: that is a model the operator may simply re-download, and
    the app already reports it as not installed, with the reason.
    """
    to_move: list[tuple[str, Path, int]] = []
    stale: list[str] = []
    for package_id in sorted(index):
        # The id names one folder directly inside the library. Anything that is not one plain path
        # component is not something this script is going to follow out of the library.
        if not package_id or package_id in (".", "..") or "/" in package_id or "\\" in package_id:
            continue
        folder = library / package_id
        if not folder.is_dir():
            if package_id not in offered:
                stale.append(package_id)
            continue
        if package_id in offered:
            continue
        to_move.append((package_id, folder, folder_bytes(folder)))
    return to_move, stale


def move_to_trash(folder: Path, library: Path, package_id: str, stamp: str | None = None) -> Path:
    """Move one package folder into `<library>/_trash/<package_id>-<YYYYMMDD-HHMMSS>/`.

    A rename inside the same library, so nothing is copied and nothing is deleted. It also means
    the space is *not* freed yet: it comes back when the operator empties `_trash`, which is his
    call and the reason the path is printed at the end of the run.
    """
    trash = library / TRASH_FOLDER
    trash.mkdir(parents=True, exist_ok=True)
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    destination = trash / f"{package_id}-{stamp}"
    suffix = 1
    while os.path.lexists(destination):  # never replace even a broken link in the owner's archive
        suffix += 1
        destination = trash / f"{package_id}-{stamp}-{suffix}"
    shutil.move(str(folder), str(destination))
    return destination


def forget_packages(library: Path, moved: list[str], stale: list[str]) -> tuple[int, int]:
    """Drop entries from the app's receipt log, in one atomic replace.

    Called after the copy step has finished writing receipts, because the log is also seeded from
    the kit's copy earlier in the run: an entry removed before that would only be carried back in.
    Returns (dropped for moved packages, dropped stale receipts). A stale receipt the log no longer
    holds is not counted - the log as it stands is what matters, and the kit's record and this
    Mac's do not have to be the same file.
    """
    index = read_index(library)
    moved_present = [package_id for package_id in moved if package_id in index]
    stale_present = [package_id for package_id in stale if package_id in index]
    if not moved_present and not stale_present:
        return (0, 0)
    for package_id in moved_present + stale_present:
        del index[package_id]
    write_index(library, index)
    return (len(moved_present), len(stale_present))


def voice_library() -> Path:
    """Where the app keeps its voices.

    The Voice worker resolves `STUDIOHUB_DATA_DIR` and appends `voice/voices`; in the packaged app
    the data root is `~/Library/Application Support/StudioHub Standalone`. That is the same path
    `import_my_voices.py` writes, so a voice copied here and a voice imported by hand are
    indistinguishable to the app.
    """
    override = os.environ.get("STUDIOHUB_VOICES_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return (Path.home() / "Library/Application Support/StudioHub Standalone/voice/voices")


def kit_voices(library: Path) -> list[tuple[Path, str]]:
    """Every voice folder the kit carries, as (folder, name).

    A voice is one folder holding `metadata.json`, the reference clip it names and, usually,
    `transcript.txt` — the shape the Voice worker reads. Anything without metadata is skipped
    rather than copied, because the app would only ignore it.
    """
    found: list[tuple[Path, str]] = []
    for folder in sorted(library.iterdir()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        metadata = folder / "metadata.json"
        if not metadata.is_file():
            continue
        try:
            name = str(json.loads(metadata.read_text()).get("name") or folder.name)
        except (OSError, ValueError):
            name = folder.name
        found.append((folder, name))
    return found


#: Timings measured for a long reference clip, written beside the transcript by
#: `qualification/time_kit_voices.py`. One name, so the tool that writes it and the copy step that
#: carries it cannot disagree.
TIMED_TRANSCRIPT = "transcript.timed.json"


def copy_voices(source: Path, destination: Path) -> tuple[int, int, list[str]]:
    """Add the kit's voices to this Mac, and leave every voice already here alone.

    Returns (added, already present, names added). A voice already on this Mac is never touched:
    the operator may have corrected a transcript or replaced a clip on that machine, and a model
    copy has no business overwriting a recording it did not make. Adding is the whole job.
    """
    if not source.is_dir():
        return (0, 0, [])
    destination.mkdir(parents=True, exist_ok=True)
    added, present, names = 0, 0, []
    for folder, name in kit_voices(source):
        target = destination / folder.name
        if target.is_dir():
            present += 1
            timed = folder / TIMED_TRANSCRIPT
            # The clip and the transcript belong to whoever recorded them, so an existing voice is
            # never overwritten. The timings are different: they are measured, not authored, the app
            # only uses them when they match the transcript exactly, and a long clip cannot be
            # prepared without them. Added later to the kit, they were skipped along with the voice
            # and never reached the Mac at all - measured on 2026-09-18, 0 of 10 voices on a node
            # had timings while the kit carried 7.
            if timed.is_file() and not (target / TIMED_TRANSCRIPT).exists():
                try:
                    shutil.copyfile(timed, target / TIMED_TRANSCRIPT)
                    names.append(f"{name} (timings added)")
                except OSError:
                    pass
            continue
        try:
            copy_folder(folder, target)
        except OSError:
            continue
        added += 1
        names.append(name)
    return (added, present, names)


def load_verifier(components: Path):
    """The app's own model code, so the record we write is exactly what the app checks."""
    if not components.is_dir():
        return None
    sys.path.insert(0, str(components))
    try:
        import model_packages  # type: ignore
    except Exception:
        return None
    return model_packages


def companion_packages(verifier, manifest: dict) -> list[str]:
    """The companions this manifest declares, read the app's way.

    `model_packages.required_packages` is the one reader of the relationship, and it honours both
    spellings a manifest may carry. Falling back to a local read of one field would reintroduce
    the split this script exists to avoid: the model whose companion is skipped is the model that
    then fails offline, on a Mac that has no network to fix it with.
    """
    if verifier is not None:
        return list(verifier.required_packages(manifest))
    declared = manifest.get("requires_packages") or manifest.get("dependencies") or []
    return [str(dependency) for dependency in declared]


def register(verifier, manifest: dict, destination: Path, catalog: dict) -> str:
    """Make sure the app's receipt exists for this model, hashing only if it has to.

    This is what keeps a model reading as installed. Without it the app compares the manifest
    against an old receipt, decides the model is not installed, and makes the operator verify
    29 GB by hand - which is the whole failure this script exists to prevent.

    A receipt that already satisfies the app is left alone, so a model that was copied months
    ago is not hashed again on every run. The stale ones are still repaired here, because the
    manifest has changed under them.

    The folder is resolved the same way the app resolves the library. On macOS `/tmp` is a link
    to `/private/tmp`, and the app refuses a folder that is not literally under the library it
    resolved, so passing the unresolved path is rejected as "outside the selected library".
    """
    if verifier is None:
        return "not recorded (the app will check this model itself)"
    library = verifier._library(destination)
    folder = library / manifest["package_id"] / manifest["revision"]
    try:
        if verifier.preflight(manifest, library, catalog=catalog)["state"] == "files_verified":
            return "already recorded"
    except Exception:
        pass  # an unreadable or stale receipt is exactly what re-registering fixes
    try:
        verifier._register(manifest, folder, library)
    except Exception as error:  # damaged on arrival: say so rather than recording a false pass
        return f"FAILED its own check: {error}"
    return "ready"


def main() -> int:
    here = Path(__file__).resolve().parent
    kit = here.parent if here.name == "Technical" else here
    library = (kit / "model-library").resolve()
    app_root = None
    # The staged bundle deliberately carries no version in its name (`packaging/stage_voice.py`),
    # so there is no version to pin: a pinned fallback would only ever be dead weight or, worse,
    # find an old copy left beside a new one.
    # **The installed app first, the kit's copy last** — the manifests that decide what fits this
    # Mac must be the ones the app that will run them actually reads, not the ones on a removable
    # drive that may be an update ahead, or behind.
    #
    # `STUDIOHUB_APP_ROOT` is honoured first and exists for two reasons: the launcher can hand the
    # helper the exact bundle it picked for the interpreter, so the two cannot choose differently,
    # and a test can point it at a fixture. **Without it these tests read the owner's real
    # `/Applications/Studio Hub.app`** — they did, silently, and started failing the moment the
    # search order changed, which is what a test coupled to a live install does.
    override = os.environ.get("STUDIOHUB_APP_ROOT", "").strip()
    candidates = []
    if override:
        candidates.append(Path(override))
    candidates += [Path("/Applications/Studio Hub.app"),
                   Path.home() / "Applications/Studio Hub.app",
                   kit / "Studio Hub.app"]
    for candidate in candidates:
        if (candidate / "Contents/Resources/model-manifests").is_dir():
            app_root = candidate
            break
    if app_root is None:
        print("  Could not find Studio Hub. Install the app first, then run this file again.")
        return 2
    resources = app_root / "Contents/Resources"
    manifests_dir = resources / "model-manifests"
    if not library.is_dir():
        print(f"  Could not find the models at:\n      {library}")
        return 2

    # The one retired-models list ships inside the app; an app that predates it retires nothing.
    sys.path.insert(0, str(resources / "components"))
    try:
        from retired_models import manifest_retired  # type: ignore
    except ImportError:
        manifest_retired = lambda manifest, stem=None: False  # noqa: E731
    # The shelved-models list (`components/shelved_models.py`) ships there too. A shelved model stays
    # in the kit - this step just does not copy it - unless this Mac is marked "keep all models" in the
    # Hub's device settings. An app that predates the list shelves nothing.
    try:
        from shelved_models import keep_all_models, manifest_shelved  # type: ignore
    except ImportError:
        manifest_shelved = lambda manifest, stem=None: False  # noqa: E731
        keep_all_models = lambda settings_file: False  # noqa: E731
    keep_all = keep_all_models(Path.home() / HUB_SETTINGS_RELATIVE)
    # **Lite Mode models only (owner 2026-10-03/04)**: the 8 GB-class models a Lite Mode Mac runs
    # (`components/workstation_models.py`, ~15 GB). The kit's `2 - Copy the Lite Mode models.command`
    # sets STUDIOHUB_WORKSTATION=1; it is the only difference from `2 - Copy the models`.
    shop_mac = os.environ.get("STUDIOHUB_WORKSTATION", "").strip() == "1"
    if shop_mac:
        try:
            from workstation_models import manifest_allowed  # type: ignore
        except ImportError:
            print("  This kit's Studio Hub predates Lite Mode, so it cannot copy only the Lite Mode models.")
            return 2
    manifests = {m["package_id"]: m for m in
                 (json.loads(p.read_text()) for p in sorted(manifests_dir.glob("*.json")))
                 if not manifest_retired(m)}
    memory = machine_memory_gb()
    # The app's own companion reader, so a package's relationship is resolved exactly as the app
    # resolves it - under either spelling a manifest may carry, and not by a second copy of the
    # rule living here. Loaded before the selection below, which is the first thing that needs it.
    verifier = load_verifier(resources / "components")
    admission_check = getattr(verifier, "production_allowed", None)
    if any("production_allowed" in manifest for manifest in manifests.values()) \
            and not callable(admission_check):
        print("  This app cannot read the model admission policy. Update Studio Hub, then run "
              "this file again.")
        return 2

    print("=" * 68)
    print("  Studio Hub - putting this Mac's models in place")
    print("=" * 68)
    print()
    print(f"  This Mac has {memory} GB of memory.")
    print()

    chosen, skipped, shelved, not_for_shop, pending, not_stocked = [], [], [], [], [], []
    for package_id, manifest in sorted(manifests.items()):
        if admission_check is not None and not admission_check(manifest):
            pending.append((package_id, manifest))
            continue
        if shop_mac and not manifest_allowed(manifest):
            not_for_shop.append(package_id)
            continue
        if not keep_all and manifest_shelved(manifest):
            shelved.append(package_id)
            continue
        if not keep_all and package_id in NOT_STOCKED:
            not_stocked.append(package_id)
            continue
        needs = required_gb(manifest)
        (chosen if needs <= memory else skipped).append((package_id, needs, manifest))

    # A model that depends on another one is no use without it, so a companion is carried along
    # even when it would not have been picked on its own. A pending companion cannot make a usable
    # package, so hold the parent back and name the relationship instead of copying either one.
    carried: list[str] = []
    blocked_by_pending: list[tuple[str, str]] = []
    selectable = []
    pending_ids = {package_id for package_id, _manifest in pending}
    for package_id, needs, manifest in chosen:
        pending_dependencies = [dependency for dependency in companion_packages(verifier, manifest)
                                if dependency in pending_ids]
        if pending_dependencies:
            blocked_by_pending.extend((package_id, dependency)
                                      for dependency in pending_dependencies)
            continue
        selectable.append((package_id, needs, manifest))
    chosen = selectable
    for package_id, _needs, manifest in list(chosen):
        for dependency in companion_packages(verifier, manifest):
            if dependency in manifests and all(dependency != c[0] for c in chosen):
                chosen.append((dependency, required_gb(manifests[dependency]), manifests[dependency]))
                carried.append(dependency)
    chosen.sort(key=lambda row: row[0])

    # **The folder the Hub is actually using** (owner report, 2026-10-18). The app writes the library
    # the operator chose to `app-preferences.json`, and that is where the models must arrive; filling
    # `~/StudioHubModels` regardless left such a Mac needing a manual repoint. A Mac that has never
    # chosen one resolves to the same canonical home-folder library the app offers, so a new Mac and
    # the kit agree by construction.
    resolver = shared_resolver()
    resolution = resolver() if resolver is not None else None
    if resolution is not None and resolution.problem:
        print(f"  {resolution.problem}.")
        print("  Nothing was copied: filling a different folder would leave the Hub reading an empty")
        if resolution.source == "unusable":
            print("  one. Open Studio Hub and choose the folder there (which writes the settings file")
            print("  again), then run this file again.")
        else:
            print("  one. Connect the drive, or choose the folder in Studio Hub, then run this again.")
        return 2
    destination = resolution.library if resolution is not None else Path.home() / LIBRARY_FOLDER
    # A Mac whose **only** library is the earlier kit's `~/Downloads/model-library` resolves to that
    # folder (`source == "legacy"`), but the app offers the canonical home folder first and the
    # folder itself sits inside `Downloads`, which macOS protects from a service the app starts. So
    # this step keeps the behaviour it always had for exactly that case: the tree is renamed out to
    # the canonical folder and filled there, and the app then recognises `~/StudioHubModels` as the
    # prepared library, so the two halves still name one folder. A folder the operator chose in the
    # app (`configured`/`app-data`) is never re-pointed: `destination` stays what the Hub reads.
    if resolution is not None and resolution.source == "legacy":
        from model_library import default_library  # type: ignore
        destination = default_library(Path.home())
    if resolution is not None and resolution.source in ("configured", "app-data"):
        print(f"  This Mac's Hub keeps its models in: {destination}")

    # An earlier kit copied into ~/Downloads/model-library. Move that tree once rather than copy
    # 34 GB again, and move it out of the protected folder while we are here. **This is the only
    # move this step makes**, it is the same one it always made (the old destination was always the
    # canonical folder), and it is never made into a folder the operator chose in the app - that
    # folder is not this step's to reorganise.
    legacy = Path.home() / LEGACY_LIBRARY_FOLDER
    canonical = resolution is None or resolution.source in ("library", "legacy", "default")
    if canonical and not destination.exists() and legacy.is_dir():
        try:
            legacy.rename(destination)
            print(f"  Moved the model library out of Downloads to {destination}")
        except OSError as exc:
            print(f"  Could not move {legacy} to {destination}: {exc}")
    elif legacy.is_dir() and legacy != destination:
        print(f"  A second library is still at {legacy}. This Mac uses {destination}; "
              "the Downloads copy can be deleted once you are happy with it.")
    destination.mkdir(parents=True, exist_ok=True)

    # What this Mac holds that the kit no longer offers. Read before anything is copied, so the
    # operator sees it in the plan and agrees to it with the same answer as the copy - nothing is
    # moved that he was not shown first.
    prune, stale_receipts = plan_prune(read_index(destination), destination, set(manifests))

    # Sort every model this Mac should have into: new, changed, or already identical.
    plan: list[tuple[str, int, dict, str]] = []
    for package_id, needs, manifest in chosen:
        source = library / package_id
        target = destination / package_id
        if not source.is_dir():
            plan.append((package_id, needs, manifest, "missing-from-kit"))
        elif needs_copy(source, target):
            plan.append((package_id, needs, manifest,
                         "update" if target.is_dir() else "new"))
        else:
            plan.append((package_id, needs, manifest, "current"))

    new = [row for row in plan if row[3] == "new"]
    update = [row for row in plan if row[3] == "update"]
    current = [row for row in plan if row[3] == "current"]
    absent = [row for row in plan if row[3] == "missing-from-kit"]

    for label, rows in (("New models", new), ("Changed models", update)):
        if rows:
            print(f"  {label} ({len(rows)}):")
            for package_id, needs, _m, _how in rows:
                print(f"      {package_id:<32} needs {needs:>2} GB")
            print()
    print(f"  Already up to date: {len(current)} model(s)")
    if current:
        for package_id, _needs, _m, _how in current[:4]:
            print(f"      {package_id}")
        if len(current) > 4:
            print(f"      ... and {len(current) - 4} more")
    print()
    if absent:
        names = ", ".join(package_id for package_id, _n, _m, _how in absent)
        print(f"  Not on this SSD, so not copied ({len(absent)}): {names}")
        print()
    if shelved:
        print(f"  Shelved, not copied ({len(shelved)}): {', '.join(shelved)}")
        print("  They stay on the SSD. To keep them on this Mac, set keep_all_models in the Hub's "
              "settings (see the release notes), then run this again.")
        print()
    if not_for_shop:
        print(f"  Lite Mode models only: {len(not_for_shop)} other model(s) stay on the SSD.")
        print()
    if not_stocked:
        print(f"  Installed on request only, not copied ({len(not_stocked)}): {', '.join(not_stocked)}")
        print("  They stay on the SSD; install one from the Gateway's Install a model... menu.")
        print()
    if pending:
        print(f"  Pending models ({len(pending)}): not yet qualified; retained on SSD")
        for package_id, _manifest in pending:
            print(f"      {package_id}")
        print()
    if blocked_by_pending:
        print("  Waiting for required pending companions:")
        for package_id, dependency in blocked_by_pending:
            print(f"      {package_id} requires {dependency} "
                  "(not yet qualified; retained on SSD)")
        print()
    if skipped:
        skip_gb = sum(size_gb(m) for _p, _n, m in skipped)
        print(f"  Leaving behind {len(skipped)} model(s), {skip_gb:.1f} GB - this Mac is too small:")
        for package_id, needs, manifest in skipped:
            print(f"      {package_id:<32} needs {needs:>2} GB")
            print(f"      {'':<32} {floor_origin(manifest)}")
        print()
        print("  Not a problem: they are for bigger Macs. The rest works normally.")
        print()

    # The prune is part of the same plan and the same confirmation. It is deliberately *after* the
    # "Leaving behind" block: a model below this Mac's tier is named by a manifest, so it is still
    # offered, so it is never a candidate here however little of it this Mac can use.
    if prune:
        prune_bytes = sum(size for _package_id, _folder, size in prune)
        print(f"  Removing {len(prune)} model(s) this kit no longer offers, "
              f"{prune_bytes / 1e9:.1f} GB -> {destination / TRASH_FOLDER}")
        for package_id, _folder, size in prune:
            print(f"      {package_id:<32} {size / 1e9:>6.1f} GB")
        print()
    if stale_receipts:
        print(f"  Forgetting {len(stale_receipts)} receipt(s) in the app's record for models this "
              f"kit no longer offers, whose folders are already gone.")
        print()

    adding_gb = sum(size_gb(m) for _p, _n, m, how in new + update)

    # Voices travel with the models but are a separate library, and they are tiny: the whole kit's
    # set is under 10 MB, so a voice is always worth adding even when no model changed.
    voices_source = kit / "voices"
    voices_target = voice_library()
    incoming_voices = kit_voices(voices_source) if voices_source.is_dir() else []
    voices_to_add = [(folder, name) for folder, name in incoming_voices
                     if not (voices_target / folder.name).is_dir()]

    print(f"  To copy now: {len(new) + len(update)} model(s), {adding_gb:.1f} GB")
    if incoming_voices:
        print(f"  Voices: {len(voices_to_add)} to add, "
              f"{len(incoming_voices) - len(voices_to_add)} already on this Mac "
              f"(voices already here are never overwritten)")
    print(f"  Going to: {destination}")
    print()
    nothing_to_copy = not new and not update
    nothing_to_remove = not prune
    if nothing_to_copy:
        print("  Everything this Mac needs is already here. Nothing to copy.")
    if nothing_to_copy and nothing_to_remove:
        if not voices_to_add:
            print("  Checking the app's record of what is installed, then stopping.")
    else:
        # A prune on its own still has to reach this prompt: it moves GB the operator may not
        # expect to lose, and "nothing to copy" used to return before ever asking.
        #
        # **`STUDIOHUB_ASSUME_YES` is how the unified update answers this for you.** The prompt is
        # for a person who double-clicked this file and should know that gigabytes are about to
        # move; when `Update this Mac.command` runs this as one of two steps it has already said
        # what it is doing, and a second stop with nobody at the keyboard is a hang rather than a
        # confirmation. It is deliberately an explicit variable rather than a piped newline: a pipe
        # would answer any *future* prompt added here as well, silently.
        if os.environ.get("STUDIOHUB_ASSUME_YES", "").strip() == "1":
            print("  Updating, as asked. (Run '2 - Copy the models.command' directly to be asked "
                  "first.)")
        else:
            answer = input(
                "  Press Return to start, or type n and press Return to stop: ").strip().lower()
            if answer == "n":
                print("  Nothing was copied or moved.")
                return 0

    # Carry the verified record across, so models that are already current keep proving
    # themselves without hashing 29 GB again. The app's own state folder must exist before any
    # receipt can be written into it, including on a Mac that has never had one.
    state = destination / ".studiohub"
    state.mkdir(mode=0o700, exist_ok=True)
    source_state = library / ".studiohub" / "installed.json"
    if source_state.is_file():
        text = source_state.read_text()
        try:  # a receipt from the app's own library always parses; otherwise start fresh
            json.loads(text)
            (state / "installed.json").write_text(text)
        except ValueError:
            pass

    catalog = {manifest["package_id"]: manifest for _p, _n, manifest in chosen}
    started = time.time()
    copied = updated = failed = 0
    archived_revision_bytes = 0

    total = len(new) + len(update)
    for index, (package_id, _needs, manifest, how) in enumerate(new + update, start=1):
        print(f"  [{index}/{total}] {package_id} ...", end="", flush=True)
        source = library / package_id
        target = destination / package_id
        try:
            copy_folder(source, target)
        except OSError:
            print(" FAILED - run this file again")
            failed += 1
            continue
        archived_revision_bytes += prune_old_revisions(target, manifest["revision"])
        outcome = register(verifier, manifest, destination, catalog)
        if how == "new":
            copied += 1
        else:
            updated += 1
        print(f" done - {outcome}")

    # Move out what this kit no longer offers. A rename on the same volume, so it costs no copy and
    # frees no space yet: the space comes back when the operator empties `_trash`, which is his call
    # and why the path is printed at the end. A package that cannot be moved is left exactly where
    # it is and named, rather than deleted to make the plan look like it worked.
    moved: list[tuple[str, int]] = []
    for package_id, folder, size in prune:
        try:
            move_to_trash(folder, destination, package_id)
        except OSError as error:
            print(f"  {package_id}: could not be moved to _trash ({error}); left where it is")
            continue
        moved.append((package_id, size))
        print(f"  {package_id}: moved to {TRASH_FOLDER}")

    # Every model this Mac should have, copied or not, must satisfy the app's check. A run that
    # copied nothing is still the repair path for a model the app calls "not installed" - a lost
    # or stale record is fixed from the files already here, without copying anything again.
    # `register` is a cheap no-op for a record the app already accepts, which is the usual case.
    repaired = 0
    for _package_id, _needs, manifest, _how in current:
        outcome = register(verifier, manifest, destination, catalog)
        if outcome not in ("already recorded", "not recorded (the app will check this model itself)"):
            repaired += 1
            print(f"  {manifest['package_id']}: {outcome}")

    # The receipt log was seeded from the kit's copy above, so this is the last word on it: the
    # moved packages and the stale receipts are dropped together, in one atomic replace. Until this
    # runs, the app's record still names the folder that is now in `_trash`.
    try:
        forgotten_moved, forgotten_stale = forget_packages(
            destination,
            [package_id for package_id, _folder, _size in prune],
            stale_receipts)
    except OSError as error:
        # A rerun removes these as stale receipts, so nothing is lost - but say so rather than
        # leaving a record that names a folder the operator just watched move.
        print(f"  Could not update the app's record ({error}); it still names what moved.")
        forgotten_moved = forgotten_stale = 0

    voices_added, voices_present, voice_names = copy_voices(voices_source, voices_target)

    # Nothing copied, nothing added, nothing moved, no record needed repairing: there is no report
    # to make, and the summary would only repeat what the lines above already said.
    if (nothing_to_copy and not moved and not forgotten_moved and not forgotten_stale
            and not voices_added and not repaired and not failed):
        return 0

    print()
    if incoming_voices:
        print(f"  Voices: added {voices_added}, already here {voices_present}"
              f"{' ' if voice_names else ''}")
        for name in voice_names:
            print(f"      + {name}")
        print(f"  Now open Studio Hub and press Refresh on the Voices page.")
        print()
    print(f"  Copied {copied} new, updated {updated}, {len(current)} already up to date"
          f"{f', {repaired} record(s) repaired' if repaired else ''}"
          f"{f', {failed} failed' if failed else ''} in {time.time() - started:.0f} seconds.")
    if archived_revision_bytes:
        print(f"  Archived {archived_revision_bytes / 1e9:.1f} GB of older model versions under "
              f"{destination / TRASH_FOLDER}; that space remains used until you empty it.")
    if moved:
        moved_gb = sum(size for _package_id, size in moved) / 1e9
        print(f"  Moved {len(moved)} model(s) this kit no longer offers, {moved_gb:.1f} GB, to:")
        print(f"      {destination / TRASH_FOLDER}")
        print("  The space comes back when you empty that folder; it is yours to sort out.")
    if forgotten_moved or forgotten_stale:
        print(f"  Removed {forgotten_moved + forgotten_stale} old receipt(s) from the app's record"
              f" ({forgotten_moved} for the model(s) moved, {forgotten_stale} already stale).")
    print()
    if new or update:
        print("  Now open Studio Hub and, when it asks for a model folder, choose:")
        print(f"      {destination}")
    elif moved or forgotten_moved or forgotten_stale:
        print("  Nothing needed copying. Open Studio Hub when you are ready.")
    else:
        print("  Nothing needed copying. Open Studio Hub and use the new voices.")
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n  Stopped.")
