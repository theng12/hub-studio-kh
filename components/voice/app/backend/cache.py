"""
HF cache inspection.

Pure-ish functions that look at the on-disk Hugging Face cache and answer:
- where is HF_HOME for this server?
- is repo X fully cached, partially cached, or absent?
- what's the on-disk size of repo X?
"""
from __future__ import annotations

import json
import os
import re
import shutil
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

from . import paths


_IMMUTABLE_REVISION = re.compile(r"^[0-9a-fA-F]{40,64}$")


def hf_home() -> Path:
    """
    Resolve HF_HOME the same way huggingface_hub does, but as an absolute path.
    Defaults match the library's defaults so we agree with `hf download`.
    """
    raw = (os.environ.get("STUDIOHUB_MODELS_DIR")
           or os.environ.get("HF_HOME")
           or os.environ.get("HUGGINGFACE_HUB_CACHE"))
    if raw:
        return Path(raw).expanduser().resolve()
    # huggingface_hub default
    return paths.models_dir()


def hub_dir() -> Path:
    return hf_home() / "hub"


def repo_cache_dir(repo: str) -> Path:
    """HF cache folder name for a repo, e.g. 'org/name' -> 'models--org--name'."""
    safe = "models--" + repo.replace("/", "--")
    return hub_dir() / safe


def snapshot_revision(repo: str) -> str | None:
    """Return the immutable revision of the locally selected HF snapshot."""
    repo_dir = repo_cache_dir(repo)
    main_ref = repo_dir / "refs" / "main"
    try:
        revision = main_ref.read_text().strip()
    except (FileNotFoundError, OSError):
        revision = ""
    if (
        _IMMUTABLE_REVISION.fullmatch(revision)
        and (repo_dir / "snapshots" / revision).is_dir()
    ):
        return revision.lower()
    snapshots = repo_dir / "snapshots"
    try:
        candidates = sorted(
            path.name for path in snapshots.iterdir()
            if path.is_dir() and _IMMUTABLE_REVISION.fullmatch(path.name)
        )
    except (FileNotFoundError, OSError):
        package_fallback = package_snapshot(repo)
        return package_fallback[0] if package_fallback is not None else None
    if len(candidates) == 1:
        return candidates[0]
    # No HF snapshot: fall back to the verified package's recorded revision so the
    # generation path has the immutable model evidence it requires.
    package_fallback = package_snapshot(repo)
    return package_fallback[0] if package_fallback is not None else None


def _unresolved_incomplete_entries(repo: str) -> list[Path]:
    """Return partial blobs that do not already have a completed sibling.

    Hugging Face resumes downloads as ``<blob>.incomplete``. If an interrupted
    downloader leaves that file behind after another attempt completes the
    same ``<blob>``, the partial is stale and must not make a usable snapshot
    look incomplete.
    """
    blobs = repo_cache_dir(repo) / "blobs"
    if not blobs.exists():
        return []
    unresolved: list[Path] = []
    try:
        for entry in blobs.iterdir():
            if not entry.name.endswith(".incomplete"):
                continue
            # A cancelled child downloader can leave a zero-byte placeholder
            # with a transport-specific suffix. It contains no resumable
            # state; keeping it would make an otherwise usable snapshot look
            # partial forever and cause every reconciliation to queue a
            # duplicate download. Only non-empty partial blobs are meaningful.
            try:
                if entry.stat().st_size == 0:
                    continue
            except (FileNotFoundError, PermissionError):
                continue
            completed = entry.with_name(entry.name.removesuffix(".incomplete"))
            if not completed.exists():
                unresolved.append(entry)
    except FileNotFoundError:
        return []
    return unresolved


def has_incomplete(repo: str) -> bool:
    return bool(_unresolved_incomplete_entries(repo))


def has_any_snapshot(repo: str) -> bool:
    snaps = repo_cache_dir(repo) / "snapshots"
    if not snaps.exists():
        return False
    try:
        return any(snaps.iterdir())
    except FileNotFoundError:
        return False


# Recognized model-weight extensions. A snapshot that contains only README /
# LICENSE / .gitattributes is NOT actually usable, so cache_state() now
# requires at least one file with one of these extensions before reporting
# "cached". This catches the silent-partial-download failure mode for gated
# repos where the user hasn't accepted the license / provided a token: HF
# downloads the public LICENSE.md + README.md (which create a snapshot dir
# and make has_any_snapshot() return True) but skips the actual weights.
_WEIGHT_EXTENSIONS = (
    ".safetensors", ".bin", ".ckpt", ".gguf",
    ".pt", ".pth", ".npz", ".mlpackage",
)


def has_weight_files(repo: str, *, revision: str | None = None) -> bool:
    """True when the requested snapshot contains at least one weight file.

    With no ``revision`` this retains the storage-inventory behavior of
    inspecting every snapshot. Runtime cache checks pass the selected immutable
    revision so a stray ``snapshots/main`` folder cannot make an unrelated or
    metadata-only immutable snapshot look executable.
    """
    snaps = repo_cache_dir(repo) / "snapshots"
    if not snaps.exists():
        return False
    if revision is not None:
        if not _IMMUTABLE_REVISION.fullmatch(revision):
            return False
        candidates = (snaps / revision,)
    else:
        try:
            candidates = tuple(snaps.iterdir())
        except (FileNotFoundError, PermissionError):
            return False
    try:
        for snap in candidates:
            if not snap.is_dir():
                continue
            for path in snap.rglob("*"):
                name = path.name
                if name.endswith(".incomplete"):
                    continue
                lower = name.lower()
                if any(lower.endswith(ext) for ext in _WEIGHT_EXTENSIONS):
                    return True
    except (FileNotFoundError, PermissionError):
        return False
    return False


def _cache_state_for_revision(repo: str, revision: str | None) -> str:
    """Evaluate a cache against one already-resolved immutable revision."""
    if not repo_cache_dir(repo).exists():
        return "absent"
    if has_incomplete(repo):
        return "partial"
    if not has_any_snapshot(repo):
        return "partial"
    # A mutable/synthetic layout can still contain all model bytes, but it
    # cannot prove which checkpoint produced them. Keep every byte in place and
    # let snapshot_download reconcile it into an official immutable snapshot.
    if revision is None:
        return "partial"
    return "cached" if has_weight_files(repo, revision=revision) else "partial"


def model_dir(repo: str) -> Path:
    """Directory holding this repo's files: the HF cache, else the verified package.

    ``repo_cache_dir`` alone points inside the Hugging Face cache, which does not exist for a
    model the application installed as a Studio Hub package. Every caller that needs to read
    model files should resolve through here so both install paths work.
    """
    if _cache_state_for_revision(repo, snapshot_revision(repo)) != "cached":
        package = package_snapshot(repo)
        if package is not None:
            return package[1]
    return repo_cache_dir(repo)


def cache_state(repo: str) -> str:
    """Returns one of: 'absent', 'partial', 'cached'.

    'partial' means there are .incomplete blobs, a started but unfinished
    snapshot, or a snapshot that has no actual weight files (the gated-repo
    partial-download failure mode — see has_weight_files()).
    A model is cached only when its selected snapshot has both an immutable
    revision and real weight files. Mutable names such as ``main`` remain
    resumable partial state rather than routable runtime evidence.

    A model installed as a verified Studio Hub package also counts: the HF-cache check is
    tried first, then the package library, so the app's own install path is usable.
    """
    state = _cache_state_for_revision(repo, snapshot_revision(repo))
    if state == "cached":
        return state
    if package_snapshot(repo) is not None:
        return "cached"
    return state


def snapshot_contains_patterns(repo: str, patterns) -> bool:
    """Verify an immutable metadata-only companion against its download list."""
    revision = snapshot_revision(repo)
    if revision is None or not _IMMUTABLE_REVISION.fullmatch(revision):
        return False
    snapshot = repo_cache_dir(repo) / "snapshots" / revision
    if not snapshot.is_dir() or has_incomplete(repo):
        return False
    try:
        names = {
            str(path.relative_to(snapshot))
            for path in snapshot.rglob("*")
            if path.is_file() and not path.name.endswith(".incomplete")
        }
    except (FileNotFoundError, PermissionError):
        return False
    required = tuple(str(pattern) for pattern in (patterns or ()))
    return bool(required) and all(
        any(fnmatch(name, pattern) for name in names) for pattern in required
    )


def disk_bytes(repo: str) -> int:
    """Total bytes this repo occupies on disk (excludes .incomplete).

    Counts blob storage *and* snapshot files that are real files rather than
    links into ``blobs/``. A cache populated by direct file download instead of
    ``snapshot_download`` leaves ``blobs/`` empty and keeps real files in the
    snapshot; such a repo previously reported zero bytes while fully cached,
    which understated its footprint to the catalog and to Studio Hub's
    memory governor. Entries are de-duplicated by inode, so a snapshot symlink
    or hardlink pointing at a blob is never counted twice.
    """
    root = repo_cache_dir(repo)
    if not root.exists():
        return 0
    total = 0
    seen: set[tuple[int, int]] = set()
    for base in (root / "blobs", root / "snapshots"):
        if not base.exists():
            continue
        try:
            for entry in base.rglob("*"):
                if entry.name.endswith(".incomplete"):
                    continue
                try:
                    if not entry.is_file():
                        continue
                    info = entry.stat()
                except (FileNotFoundError, PermissionError, OSError):
                    continue
                key = (info.st_dev, info.st_ino)
                if key in seen:
                    continue
                seen.add(key)
                total += info.st_size
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return total


def snapshot_disk_bytes(repo: str, revision: str | None) -> int:
    """Bytes proven to belong to one immutable snapshot revision.

    Download progress must not count every completed byte in a repository's
    cache.  Historical ``snapshots/main`` layouts and other revisions are
    useful inventory, but they are not evidence that the requested immutable
    snapshot is reusable.  Walking only the exact snapshot also follows its
    normal Hugging Face blob symlinks, while inode de-duplication prevents a
    hardlinked file from being counted twice.
    """
    if revision is None or not _IMMUTABLE_REVISION.fullmatch(revision):
        return 0
    root = repo_cache_dir(repo) / "snapshots" / revision.lower()
    if not root.is_dir():
        return 0
    total = 0
    seen: set[tuple[int, int]] = set()
    try:
        entries = root.rglob("*")
        for entry in entries:
            if entry.name.endswith(".incomplete"):
                continue
            try:
                if not entry.is_file():
                    continue
                info = entry.stat()
            except (FileNotFoundError, PermissionError, OSError):
                continue
            key = (info.st_dev, info.st_ino)
            if key in seen:
                continue
            seen.add(key)
            total += info.st_size
    except (FileNotFoundError, PermissionError, OSError):
        return 0
    return total


def incomplete_bytes(repo: str) -> int:
    """Total bytes still needed by unresolved partial blobs."""
    total = 0
    for entry in _unresolved_incomplete_entries(repo):
        try:
            total += entry.stat().st_size
        except (FileNotFoundError, PermissionError):
            continue
    return total


def prune_stale_incomplete(
    repo: str, *, complete_snapshot_verified: bool = False
) -> dict[str, int]:
    """Delete stale partial blobs without discarding resumable downloads.

    Exact completed siblings are always safe to remove. Other partials are
    removed only after the caller independently verifies that the completed
    blob bytes equal the repository's current official manifest.
    """
    blobs = repo_cache_dir(repo) / "blobs"
    removed_files = 0
    removed_bytes = 0
    if not blobs.exists():
        return {"removed_files": 0, "removed_bytes": 0}
    try:
        entries = list(blobs.iterdir())
    except FileNotFoundError:
        return {"removed_files": 0, "removed_bytes": 0}
    for entry in entries:
        if not entry.name.endswith(".incomplete"):
            continue
        completed = entry.with_name(entry.name.removesuffix(".incomplete"))
        if not completed.exists() and not complete_snapshot_verified:
            try:
                # Empty placeholders are safe to remove: unlike a non-empty
                # HF partial they cannot contribute resumable bytes.
                if entry.stat().st_size != 0:
                    continue
            except (FileNotFoundError, PermissionError):
                continue
        try:
            size = entry.stat().st_size
            entry.unlink()
        except (FileNotFoundError, PermissionError):
            continue
        removed_files += 1
        removed_bytes += size
    return {"removed_files": removed_files, "removed_bytes": removed_bytes}


def _manifest_catalog() -> dict[str, tuple[str, str]]:
    """Map a model repository to (package_id, revision) from the packaged manifests."""
    location = (os.environ.get("STUDIOHUB_MODEL_MANIFESTS") or "").strip()
    if not location:
        return {}
    root = Path(location).expanduser()
    catalog: dict[str, tuple[str, str]] = {}
    try:
        manifests = sorted(root.glob("*.json"))
    except OSError:
        return {}
    for path in manifests:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        model_id = manifest.get("model_id")
        revision = manifest.get("revision")
        package_id = manifest.get("package_id")
        if (isinstance(model_id, str) and isinstance(revision, str)
                and isinstance(package_id, str)):
            catalog[model_id] = (package_id, revision.lower())
    return catalog


def _view_is_sound(view: Path) -> bool:
    """Whether a cached view still points at files that exist.

    The view is built from absolute symlinks, so it is only valid while the library it was built
    from stays where it was. Move or replace the library - which is exactly what happens when a
    model folder is tidied up, or when the app is pointed at a different library - and every link
    in the view dangles while the marker still says the view is finished. mlx-audio then fails with
    "Config not found" for a model whose config is sitting right there in the library, and the view
    is never rebuilt because the marker is present.

    Measured on 2026-09-18: whisper-base and whisper-small failed this way after their library
    moved, while whisper-turbo kept working because it needs no view at all.

    A dangling link is found by ``exists()`` being false while ``is_symlink()`` is true. Nothing
    else in the view is checked: the marker plus a resolved link set is what "finished" means, and
    an unsound view is removed so the caller rebuilds it against the current library.

    ponytail: reachability, not identity. A library replaced in place by a *different* revision of
    the same package leaves every link resolving, so this reuses a view that no longer matches the
    bytes it was built from. Every path the package is registered under carries its revision
    (``<package>/<40-hex>/...``), and the marker records ``package@revision``, so replacing a model
    means a new directory and a new view name - which is why reachability is enough here. Upgrade
    path if that ever stops holding: compare the marker's revision against the manifest revision.
    """
    try:
        usable = 0
        for entry in view.iterdir():
            if entry.name.startswith("."):
                continue
            if not entry.exists():
                # Present in name only: the library it was linked from has moved.
                shutil.rmtree(view, ignore_errors=True)
                return False
            usable += 1
    except OSError:
        return False
    if not usable:
        # An empty view is not a finished view. Requiring only "nothing dangles" would call a
        # directory with no entries sound, which is precisely the state that failed: present on
        # disk, so reused forever, and containing nothing mlx-audio could load.
        shutil.rmtree(view, ignore_errors=True)
        return False
    return True


def _merged_snapshot_view(package_id: str, revision: str, folder: Path) -> Path | None:
    """Build (once) a read-only snapshot view that mlx-audio can actually load.

    A Studio Hub package separates ``<revision>/model/`` (config + weights) from
    ``<revision>/companions/processor/`` (tokenizer/processor), while Hugging Face snapshot
    layout — which mlx-audio expects — has both side by side. Handed only ``model/``,
    mlx-audio loads the weights and silently falls back to an empty tokenizer, which then
    raises ``IndexError`` from ``encode(" -")[0]`` during decoding.

    The view is built under the app data directory with symlinks, never inside the model
    library, so verified model bytes are untouched.
    """
    # The Subtitle worker carries its own state root, so accept either name for the view.
    state = os.environ.get("STUDIOHUB_DATA_DIR") or os.environ.get("STUDIOHUB_SUBTITLE_DATA_DIR")
    if not state:
        return None
    view = Path(state) / "snapshot-views" / f"{package_id}-{revision[:16]}"
    marker = view / ".studiohub-view"
    if marker.is_file() and _view_is_sound(view):
        return view
    weights = folder / "model" if (folder / "model").is_dir() else folder
    sources = [weights, folder / "companions" / "processor"]
    try:
        view.mkdir(parents=True, exist_ok=True)
        for source in sources:
            if not source.is_dir():
                continue
            for entry in source.iterdir():
                link = view / entry.name
                if link.exists() or link.is_symlink():
                    continue
                os.symlink(entry.resolve(), link)
        # mlx-audio's Whisper loader globs ``*.safetensors``. Some pinned packages (whisper
        # base/small) ship ``weights.npz`` instead, which is why they were "catalog-only".
        # Convert once into the view; the verified library bytes are never touched.
        if not any(view.glob("*.safetensors")):
            import mlx.core as mx  # local import: only needed for the conversion path
            for npz in sorted(weights.glob("*.npz")):
                arrays = mx.load(str(npz))
                mx.save_safetensors(str(view / (npz.stem + ".safetensors")), dict(arrays))
        marker.write_text(f"{package_id}@{revision}\n", encoding="utf-8")
    except (OSError, ValueError, ImportError):
        return None
    return view


def package_snapshot(repo: str) -> tuple[str, Path] | None:
    """Resolve a repository through the Studio Hub verified-package library.

    This module historically understood only an HF cache
    (``<hf_home>/hub/models--org--name``). The application installs models as
    verified packages at ``<library>/<package_id>/<revision>/`` and records them in
    ``<library>/.studiohub/installed.json`` only after verification succeeded. Without
    this bridge every installed model reported ``absent``, so the Hub refused to
    dispatch Voice work even though the app called the model installed and verified.

    Only a package present in that index at the manifest's exact revision counts; a
    bare folder or a stale revision must never be advertised as downloaded.
    """
    entry = _manifest_catalog().get(repo)
    if entry is None:
        return None
    package_id, revision = entry
    root = hf_home()
    try:
        index = json.loads((root / ".studiohub" / "installed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(index, dict):
        return None
    row = index.get(package_id)
    if not isinstance(row, dict):
        return None
    if str(row.get("revision") or "").lower() != revision:
        return None
    folder = row.get("folder")
    if not isinstance(folder, str) or not folder:
        return None
    candidate = root / folder
    try:
        if not candidate.is_dir():
            return None
    except OSError:
        return None
    # A Studio Hub package keeps the model files under a ``model/`` subfolder next to
    # ``companions/``, while the Hugging Face snapshot layout (which every caller here was
    # written for) has the files at the folder root. Prefer a merged view that presents both
    # halves together; fall back to the weights folder alone.
    merged = _merged_snapshot_view(package_id, revision, candidate)
    if merged is not None:
        return revision, merged
    weights = candidate / "model"
    try:
        if weights.is_dir():
            candidate = weights
    except OSError:
        pass
    return revision, candidate


def status_snapshot(repo: str) -> dict:
    revision = snapshot_revision(repo)
    state = _cache_state_for_revision(repo, revision)
    path = str(repo_cache_dir(repo)) if state != "absent" else None
    if state == "absent":
        # Fall back to the verified package library before declaring the model missing.
        package = package_snapshot(repo)
        if package is not None:
            revision, folder = package
            state, path = "cached", str(folder)
    return {
        "repo": repo,
        "state": state,
        # Advertise the exact cached Hugging Face snapshot before dispatch so
        # Studio Hub can match a GenStudio-pinned request to the right worker.
        # Mutable refs such as ``main`` are never returned here.
        "snapshot_revision": revision,
        "path": path,
        "bytes_complete": disk_bytes(repo),
        "bytes_incomplete": incomplete_bytes(repo),
    }


def ensure_hub_dir() -> Path:
    d = hub_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d
