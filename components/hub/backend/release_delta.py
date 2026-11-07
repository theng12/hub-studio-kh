"""Stage an update as a copy of the installed app plus only the files that changed.

The installed app is 5 GB and consecutive releases differ by a few files (0.5 MB). Instead of
downloading and unpacking a 1.8 GB disk image, the app is cloned (APFS ``cp -c``: seconds, almost no
extra disk), the files that differ from the release's manifest are fetched by sha256 and written
beside the originals, and the result is verified against the manifest. The native app then does what
it has always done with a staged app: verify it, swap it in, restore the old one on failure.

Nothing here touches the running app. Every write goes to
``<Applications>/.studiohub-staged-<id>/<App>.app``, and a failure - or a crash - leaves only that
directory behind. Anything that cannot be proved (no manifest, a blob that does not hash, a path
that escapes the bundle) raises :class:`DeltaRefused`, which the caller turns into "use the full
disk image instead".
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Mapping

from . import file_manifest as fm

STAGED_PREFIX = ".studiohub-staged-"
# A delta that would fetch more than this fraction of the signed disk image is not worth its
# complexity: the image is compressed, and a release that changed that much is a full install.
MAX_DELTA_FRACTION = 0.5
# Head-room beyond the bytes we know we will write: filesystem metadata, and the blobs in flight.
SPACE_SLACK = 256 * 1024 * 1024

Progress = Callable[[str, int, int], None]
# (sha256, destination, expected size) -> writes the bytes at destination or raises DeltaRefused.
BlobFetcher = Callable[[str, Path, int], None]


class DeltaRefused(Exception):
    """The delta cannot be applied safely; ``code`` says why, and the full image is the fallback."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


# ── the installed app ──────────────────────────────────────────────────────

_installed_cache: dict[tuple, dict[str, fm.Entry]] = {}
_probe_lock = threading.Lock()
_probe_thread: threading.Thread | None = None
_probe_failed: tuple | None = None   # identity key whose measurement failed: null until it changes


def installed_app_root() -> Path | None:
    """The ``.app`` this Hub is running from, or None (a checkout, a test, a Voice-only run)."""
    raw = os.environ.get("STUDIOHUB_RELEASE_MANIFEST")
    if not raw:
        return None
    path = Path(raw)
    resources = path.parent
    if (path.name != "release-manifest.json" or resources.name != "Resources"
            or resources.parent.name != "Contents" or resources.parent.parent.suffix != ".app"):
        return None
    return resources.parent.parent


def _identity_stamp(app: Path) -> int | None:
    """The installed identity's mtime, or None when the file the app was started with is gone."""
    try:
        return (app / "Contents/Resources/release-manifest.json").stat().st_mtime_ns
    except OSError:
        return None


def installed_entries(app: Path) -> dict[str, fm.Entry]:
    """What the installed app contains: its actual bytes, hashed once per installed identity
    (its ``release-manifest.json``; about 20 s for 90,000 files) and remembered until that
    identity changes.

    The app's own embedded manifest is never the answer: 0.6.224 generated it pre-seal, so its
    records can name a digest the native no longer has and omit files codesign created after it.
    shortcut: a file edited after this measurement stays invisible until the identity changes;
    stage() clears the cache before it measures the bytes it clones, and every reuse still has
    to pass verify_tree's or the fetch check's sha256 before anything is installed."""
    stamp = _identity_stamp(app) or 0
    key = (str(app), stamp)
    cached = _installed_cache.get(key)
    if cached is not None:
        return cached
    entries = fm.scan(app)
    _installed_cache.clear()
    _installed_cache[key] = entries
    return entries


def validated_installed_baseline() -> dict[str, fm.Entry] | None:
    """The installed app's measured bytes, or None when this Hub cannot prove a baseline: it is
    not running from an installed app, its identity file is gone, or its tree cannot be read.
    The only state in which a delta capability may be offered."""
    app = installed_app_root()
    if app is None or _identity_stamp(app) is None:
        return None
    try:
        return installed_entries(app)
    except (fm.ManifestError, OSError):
        return None


def baseline_ready() -> bool:
    """Whether a validated baseline exists for this probe, without ever waiting for one.

    A cold measurement of a real app takes about 20 s - as long as a gateway's probe timeout -
    so this answers False at once and ensures exactly one background measurement is running;
    it fills the cache this then answers from. A failed measurement stays False (no rescan
    loop) until the installed identity changes."""
    app = installed_app_root()
    if app is None:
        return False
    stamp = _identity_stamp(app)
    if stamp is None:
        return False
    key = (str(app), stamp)
    global _probe_thread
    with _probe_lock:
        if key == _probe_failed:
            return False
        if key in _installed_cache:
            return True
        if _probe_thread is None or not _probe_thread.is_alive():
            _probe_thread = threading.Thread(target=_measure_baseline, args=(key,),
                                             name="baseline-measure", daemon=True)
            _probe_thread.start()
        return False


def _measure_baseline(key: tuple) -> None:
    global _probe_failed
    if validated_installed_baseline() is None:
        with _probe_lock:
            _probe_failed = key


_by_sha_cache: list = []   # [(entries, by_sha)], valid while `installed_entries` hands back the same dict


def installed_by_sha(app: Path) -> dict[str, str]:
    """sha256 -> one relative path in the installed app that holds those bytes. Built once per
    installed app (it walks ~93,000 entries), not once per blob served or pushed. Read-only."""
    entries = installed_entries(app)
    if _by_sha_cache and _by_sha_cache[0][0] is entries:
        return _by_sha_cache[0][1]
    found: dict[str, str] = {}
    for path, (kind, _x, _size, digest) in entries.items():
        if kind == "f":
            found.setdefault(digest, path)
    _by_sha_cache[:] = [(entries, found)]
    return found


# ── the plan ───────────────────────────────────────────────────────────────


class Plan:
    def __init__(self, old: Mapping[str, fm.Entry], new: Mapping[str, fm.Entry]) -> None:
        self.remove = sorted(path for path in old if path not in new)
        self.write: list[str] = []     # content (or a link) that differs
        self.chmod: list[str] = []     # same bytes, different executable bit
        for path, entry in new.items():
            was = old.get(path)
            if was == entry:
                continue
            if (was and was[0] == "f" and entry[0] == "f" and was[2] == entry[2]
                    and was[3] == entry[3]):
                self.chmod.append(path)
            else:
                self.write.append(path)
        self.write.sort()
        self.chmod.sort()


def fetch_bytes(plan: Plan, new: Mapping[str, fm.Entry], have: Mapping[str, str]) -> dict[str, int]:
    """The blobs that must be fetched (sha256 -> size): changed files whose bytes the installed
    app does not already hold at some other path."""
    wanted: dict[str, int] = {}
    for path in plan.write:
        kind, _x, size, digest = new[path]
        if kind == "f" and digest not in have:
            wanted[digest] = size
    return wanted


# ── writing ────────────────────────────────────────────────────────────────


class _Dirs:
    """Installed apps are 0555; the clone inherits that. Make what we must write into writable,
    and put every mode back afterwards so the staged app is not left looser than the original."""

    def __init__(self) -> None:
        self.saved: dict[Path, int] = {}

    def writable(self, directory: Path) -> None:
        if directory not in self.saved:
            mode = stat.S_IMODE(directory.lstat().st_mode)
            self.saved[directory] = mode
            if not mode & 0o200:
                os.chmod(directory, mode | 0o200)

    def restore(self) -> None:
        for directory in sorted(self.saved, key=lambda item: len(item.parts), reverse=True):
            try:
                os.chmod(directory, self.saved[directory])
            except OSError:
                pass


def _rmtree_force(path: Path) -> None:
    def fix(function, target, _exc):
        os.chmod(os.path.dirname(target), 0o755)
        os.chmod(target, 0o755)
        function(target)
    shutil.rmtree(path, onerror=fix)


def _inside(root: Path, rel: str, dirs: _Dirs, *, create: bool = True) -> Path | None:
    """The destination for ``rel``, with every parent proved to be a real directory inside
    ``root`` - a symlinked parent is how a manifest entry would write outside the bundle.
    ``None`` when a parent is absent and ``create`` is False."""
    try:
        fm.safe_relpath(rel)
    except fm.ManifestError as exc:
        raise DeltaRefused("unsafe", str(exc)) from exc
    current = root
    for part in rel.split("/")[:-1]:
        current = current / part
        if current.is_symlink():
            raise DeltaRefused("unsafe", f"{rel} would be written through a symlink")
        if not current.exists():
            if not create:
                return None
            dirs.writable(current.parent)
            current.mkdir(mode=0o755)
        elif not current.is_dir():
            raise DeltaRefused("unsafe", f"{rel} would be written inside a file")
    return root / rel


def _check_link(rel: str, target: str) -> None:
    if target.startswith("/"):
        raise DeltaRefused("unsafe", f"{rel} is a symlink to an absolute path")
    resolved = os.path.normpath(os.path.join(os.path.dirname(rel), target))
    if resolved == ".." or resolved.startswith("../"):
        raise DeltaRefused("unsafe", f"{rel} is a symlink that leaves the app")


def _place(root: Path, rel: str, entry: fm.Entry, source: Path | None, dirs: _Dirs) -> None:
    """Write one entry beside the original and rename it over: never in place, because a Mach-O
    file rewritten under a cached code signature is killed on launch."""
    destination = _inside(root, rel, dirs)
    parent = destination.parent
    dirs.writable(parent)
    temporary = parent / f".studiohub-tmp-{uuid.uuid4().hex}"
    kind, executable, _size, digest = entry
    try:
        if kind == "l":
            _check_link(rel, digest)
            os.symlink(digest, temporary)
        else:
            shutil.copyfile(source, temporary)
            os.chmod(temporary, 0o755 if executable else 0o644)
        if destination.is_dir() and not destination.is_symlink():
            _rmtree_force(destination)
        os.replace(temporary, destination)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def _remove(root: Path, rel: str, dirs: _Dirs) -> None:
    destination = _inside(root, rel, dirs, create=False)
    if destination is None or not os.path.lexists(destination):
        return
    dirs.writable(destination.parent)
    if destination.is_dir() and not destination.is_symlink():
        _rmtree_force(destination)
    else:
        os.unlink(destination)
    # A directory this emptied is not part of the release either.
    parent = destination.parent
    while parent != root:
        dirs.writable(parent.parent)
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def verify_tree(root: Path, new: Mapping[str, fm.Entry], changed: set[str]) -> None:
    """Prove the staged app is the manifest: every entry present with the right size, executable
    bit and link target, no entry that is not listed, and the bytes of every changed file re-read
    from disk and hashed."""
    seen: set[str] = set()
    for rel, info, full in fm.walk(root):
        entry = new.get(rel)
        if entry is None:
            raise DeltaRefused("corrupt", f"the staged app holds {rel}, which the release does not list")
        seen.add(rel)
        kind, executable, size, digest = entry
        if stat.S_ISLNK(info.st_mode):
            if kind != "l" or os.readlink(full) != digest:
                raise DeltaRefused("corrupt", f"{rel} is not the symlink the release lists")
            continue
        if kind != "f" or info.st_size != size:
            raise DeltaRefused("corrupt", f"{rel} is not the size the release lists")
        if bool(info.st_mode & stat.S_IXUSR) != executable:
            raise DeltaRefused("corrupt", f"{rel} has the wrong executable bit")
        if rel in changed and fm.sha256_file(full) != digest:
            raise DeltaRefused("corrupt", f"{rel} does not hash to the release's sha256")
    missing = new.keys() - seen
    if missing:
        raise DeltaRefused("corrupt", f"the staged app is missing {sorted(missing)[0]}")


# ── space and cloning ──────────────────────────────────────────────────────


def _clone(installed: Path, staged: Path, needed: int) -> str:
    """APFS clone when possible; otherwise a real copy, but only if it fits."""
    try:
        subprocess.run(["/bin/cp", "-c", "-R", str(installed), str(staged)],
                       check=True, capture_output=True)
        return "clone"
    except (subprocess.CalledProcessError, OSError):
        if staged.exists():
            _rmtree_force(staged)
    size = sum(entry[2] for entry in installed_entries(installed).values())
    free = shutil.disk_usage(staged.parent).free
    if free < size + needed + SPACE_SLACK:
        raise DeltaRefused("no_space", f"copying the app needs {size + needed + SPACE_SLACK} bytes "
                                       f"and only {free} are free")
    try:
        subprocess.run(["/bin/cp", "-R", "-p", str(installed), str(staged)],
                       check=True, capture_output=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        if staged.exists():
            _rmtree_force(staged)
        raise DeltaRefused("no_space", f"the app could not be copied: {exc}") from exc
    return "copy"


def sweep_stale(parent: Path, keep: Path | None = None, older_than: float = 3600.0) -> None:
    """Remove staging directories a crashed run left behind."""
    now = time.time()
    try:
        names = [name for name in os.listdir(parent) if name.startswith(STAGED_PREFIX)]
    except OSError:
        return
    for name in names:
        path = parent / name
        try:
            if path != keep and now - path.stat().st_mtime > older_than:
                _rmtree_force(path)
        except OSError:
            pass


# ── the whole thing ────────────────────────────────────────────────────────


def stage(installed: Path, new: Mapping[str, fm.Entry], files_sha256: str, get_blob: BlobFetcher, *,
          artifact_bytes: int | None = None, progress: Progress | None = None,
          clone: Callable[[Path, Path, int], str] = _clone) -> tuple[Path, dict]:
    """Build ``<parent>/.studiohub-staged-<id>/<App>.app`` from the installed app and a manifest.

    Returns ``(staged app, summary)``. Raises :class:`DeltaRefused` and leaves nothing behind.
    """
    report = progress or (lambda _phase, _done, _total: None)
    # Refuse an unsafe manifest before anything is copied or fetched.
    for rel, entry in new.items():
        try:
            fm.safe_relpath(rel)
        except fm.ManifestError as exc:
            raise DeltaRefused("unsafe", str(exc)) from exc
        if entry[0] == "l":
            _check_link(rel, entry[3])
    # The plan is built from a fresh measurement of the bytes about to be cloned: a view cached
    # before a file was edited would reuse it without the edit ever being read.
    _installed_cache.clear()
    old = installed_entries(installed)
    plan = Plan(old, new)
    have = installed_by_sha(installed)
    wanted = fetch_bytes(plan, new, have)
    total = sum(wanted.values())
    if artifact_bytes and total > artifact_bytes * MAX_DELTA_FRACTION:
        # The caller passes ``artifact_bytes`` only where a disk image can actually be installed:
        # a files-only release has none, so a refusal here would send the app to a 404.
        raise DeltaRefused("over_threshold",
                           f"{total} bytes changed against a {artifact_bytes}-byte disk image")

    holder = installed.parent / f"{STAGED_PREFIX}{uuid.uuid4().hex}"
    staged = holder / installed.name
    sweep_stale(installed.parent, keep=holder)
    holder.mkdir(mode=0o700)
    dirs = _Dirs()
    try:
        report("copying", 0, total)
        method = clone(installed, staged, total)
        blobs = holder / ".blobs"
        blobs.mkdir()
        done = 0
        fetched: dict[str, Path] = {}
        for digest, size in sorted(wanted.items()):
            report("downloading", done, total)
            target = blobs / digest
            get_blob(digest, target, size)
            if not target.is_file() or target.stat().st_size != size or fm.sha256_file(target) != digest:
                raise DeltaRefused("corrupt", f"the bytes fetched for {digest} are not that sha256")
            fetched[digest] = target
            done += size
        report("applying", done, total)
        for rel in plan.remove:
            _remove(staged, rel, dirs)
        for rel in plan.write:
            entry = new[rel]
            source = None
            if entry[0] == "f":
                source = fetched.get(entry[3])
                if source is None:
                    original = installed / have[entry[3]]
                    if original.is_symlink() or original.stat().st_size != entry[2]:
                        raise DeltaRefused("corrupt", f"{have[entry[3]]} is not the file the release reuses")
                    source = original
            _place(staged, rel, entry, source, dirs)
        for rel in plan.chmod:
            target = _inside(staged, rel, dirs)
            dirs.writable(target.parent)
            os.chmod(target, 0o755 if new[rel][1] else 0o644)
        shutil.rmtree(blobs)
        for rel, _info, _full in list(fm.walk(staged)):
            if rel not in new:
                _remove(staged, rel, dirs)
        report("verifying", total, total)
        verify_tree(staged, new, set(plan.write))
        # The installed app's manifest directory describes the bytes this delta just replaced,
        # and a manifest written post-seal would break the signature anyway: the staged app
        # carries none, and the next update measures the installed bytes themselves.
        stale = staged / fm.DIR_REL
        dirs.writable(stale.parent)
        if stale.exists():
            _rmtree_force(stale)
        # What the descriptor signed must be what the release's entries serialize to - checked
        # in memory; verify_tree above already proved the staged bytes are those entries.
        if fm.build(new)[0]["files_sha256"] != files_sha256:
            raise DeltaRefused("corrupt", "the staged app does not reproduce the signed files_sha256")
    except BaseException:
        dirs.restore()
        _rmtree_force(holder)
        raise
    dirs.restore()
    return staged, {"method": method, "files_changed": len(plan.write),
                    "files_removed": len(plan.remove), "bytes_fetched": total}
