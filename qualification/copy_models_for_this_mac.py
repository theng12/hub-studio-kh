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
    still reads as installed instead of needing to be re-verified by hand.

Nothing is ever deleted except an old revision folder of a model that was just updated.
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

# Some models are shipped with a conservative figure in their manifest, and the owner has run
# them on smaller Macs many times. Raising a model's floor here is a deliberate, reviewable act;
# lowering one is not, so there is no way to say "needs less than the manifest says" - only
# "the owner has proven this one runs smaller".
PROVEN_SMALLER = {
    # FLUX.2 Klein 4B: the manifest says 16 GB, the owner has produced tens of thousands of
    # images on 8 GB Macs. The 16 GB figure is a qualification tier, not a requirement.
    "flux2-klein-4b-mlx-4bit": 8,
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
    qualification = manifest.get("qualification") or {}
    floor = qualification.get("minimum_unified_memory_gb")
    floor = int(floor) if isinstance(floor, (int, float)) and floor > 0 else 0
    return PROVEN_SMALLER.get(manifest["package_id"], floor)


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

    The old folder is moved aside first and only deleted once the new one is in place, so an
    interrupted run leaves either the previous good copy or the fully copied new one.
    """
    staging = target.with_name(target.name + ".incoming")
    shutil.rmtree(staging, ignore_errors=True)
    result = subprocess.run(["/bin/cp", "-Rp", str(source), str(staging)])
    if result.returncode != 0:
        shutil.rmtree(staging, ignore_errors=True)
        raise OSError(f"could not copy {source.name}")
    retired = target.with_name(target.name + ".replaced")
    shutil.rmtree(retired, ignore_errors=True)
    if target.exists():
        target.rename(retired)
    try:
        staging.rename(target)
    except OSError:
        if retired.exists() and not target.exists():
            retired.rename(target)
        raise
    shutil.rmtree(retired, ignore_errors=True)


def prune_old_revisions(target: Path, keep: str) -> int:
    """Delete revision folders of a just-updated model that the current manifest no longer names.

    A model folder holds one subfolder per revision. Copying a new revision in would otherwise
    leave the previous 24 GB revision next to it forever, doubling what each Mac carries.
    """
    freed = 0
    try:
        children = sorted(target.iterdir())
    except OSError:
        return 0
    for child in children:
        if not child.is_dir() or child.name == keep:
            continue
        if len(child.name) != 40 or any(c not in "0123456789abcdef" for c in child.name):
            continue  # not a revision folder: leave anything unfamiliar alone
        freed += sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
        shutil.rmtree(child, ignore_errors=True)
    return freed


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
    for candidate in (kit / "Studio Hub.app", kit / "Studio Hub 0.4.0.app",
                      Path("/Applications/Studio Hub.app"),
                      Path("/Applications/Studio Hub 0.4.0.app"),
                      Path.home() / "Applications/Studio Hub.app",
                      Path.home() / "Applications/Studio Hub 0.4.0.app"):
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

    manifests = {m["package_id"]: m for m in
                 (json.loads(p.read_text()) for p in sorted(manifests_dir.glob("*.json")))}
    memory = machine_memory_gb()

    print("=" * 68)
    print("  Studio Hub - putting this Mac's models in place")
    print("=" * 68)
    print()
    print(f"  This Mac has {memory} GB of memory.")
    print()

    chosen, skipped = [], []
    for package_id, manifest in sorted(manifests.items()):
        needs = required_gb(manifest)
        (chosen if needs <= memory else skipped).append((package_id, needs, manifest))

    # A model that depends on another one is no use without it, so a companion is carried along
    # even when it would not have been picked on its own.
    carried: list[str] = []
    for package_id, _needs, manifest in list(chosen):
        for dependency in manifest.get("requires_packages", []) or []:
            if dependency in manifests and all(dependency != c[0] for c in chosen):
                chosen.append((dependency, required_gb(manifests[dependency]), manifests[dependency]))
                carried.append(dependency)
    chosen.sort(key=lambda row: row[0])

    destination = Path.home() / "Downloads" / "model-library"
    destination.mkdir(parents=True, exist_ok=True)

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
    if skipped:
        skip_gb = sum(size_gb(m) for _p, _n, m in skipped)
        print(f"  Leaving behind {len(skipped)} model(s), {skip_gb:.1f} GB - this Mac is too small:")
        for package_id, needs, _m in skipped:
            print(f"      {package_id:<32} needs {needs:>2} GB")
        print()
        print("  Not a problem: they are for bigger Macs. The rest works normally.")
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
    if nothing_to_copy:
        print("  Everything this Mac needs is already here. Nothing to copy.")
        if not voices_to_add:
            print("  Checking the app's record of what is installed.")
    else:
        answer = input(
            "  Press Return to start, or type n and press Return to stop: ").strip().lower()
        if answer == "n":
            print("  Nothing was copied.")
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

    verifier = load_verifier(resources / "components")
    catalog = {manifest["package_id"]: manifest for _p, _n, manifest in chosen}
    started = time.time()
    copied = updated = failed = 0
    freed = 0

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
        freed += prune_old_revisions(target, manifest["revision"])
        outcome = register(verifier, manifest, destination, catalog)
        if how == "new":
            copied += 1
        else:
            updated += 1
        print(f" done - {outcome}")

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

    voices_added, voices_present, voice_names = copy_voices(voices_source, voices_target)

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
    if freed:
        print(f"  Removed {freed / 1e9:.1f} GB of older model versions that are no longer used.")
    print()
    if new or update:
        print("  Now open Studio Hub and, when it asks for a model folder, choose:")
        print(f"      {destination}")
    else:
        print("  Nothing needed copying. Open Studio Hub and use the new voices.")
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n  Stopped.")
