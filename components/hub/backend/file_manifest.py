"""The per-file manifest of a Studio Hub app bundle, and what can be said about it.

A release is 5 GB and 90,000 files; consecutive releases differ by a handful of them. This module
is the one description of "what is in the app" that the stager writes, a controller stores, and a
node diffs against its own installed copy, so that only the files that changed are sent.

Deliberately stdlib-only and import-free of the rest of the Hub: `packaging/stage_voice.py` loads it
by path to write the manifest, and the Hub imports it to read one.

Layout, under ``Contents/Resources/file-manifest/`` (the directory is not itself listed):

* ``index.json`` - the groups, each with the sha256 of its **uncompressed** TSV (gzip bytes are not
  a stable identity), plus ``files_sha256`` / ``files_count`` / ``files_bytes`` for the whole app.
* one gzipped TSV per group: ``core`` (everything outside ``runtimes/``) and one
  ``runtime-<name>`` per ``Contents/Resources/runtimes/<name>``.

A TSV line is ``type<TAB>x<TAB>size<TAB>sha256-or-link-target<TAB>path``, sorted by path:
``f`` a regular file, ``l`` a symlink (size 0, the target in place of the digest). ``x`` is the
owner-execute bit and **only** that bit: an installed app is 0444/0555 and the same file in a DMG
is 0644/0755, so full modes would make every file "changed". ``.DS_Store`` is never listed.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Iterable, Mapping

SCHEMA = "studiohub.file-manifest.v1"
DIR_REL = "Contents/Resources/file-manifest"
INDEX_NAME = "index.json"
IGNORED_NAMES = frozenset({".DS_Store"})
RUNTIMES_PREFIX = "Contents/Resources/runtimes/"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_GROUP = re.compile(r"^(?:core|runtime-[A-Za-z0-9_.+-]{1,64})$")

# (kind, executable, size, digest-or-target)
Entry = tuple[str, bool, int, str]


class ManifestError(ValueError):
    """The manifest (or a tree measured against it) is not what it claims to be."""


def group_of(path: str) -> str:
    if path.startswith(RUNTIMES_PREFIX):
        name, _, rest = path[len(RUNTIMES_PREFIX):].partition("/")
        if name and rest:
            return f"runtime-{name}"
    return "core"


def group_prefix(group: str) -> str:
    return "" if group == "core" else f"{RUNTIMES_PREFIX}{group[len('runtime-'):]}/"


def safe_relpath(path: str) -> str:
    """A path that stays inside the bundle: relative, no ``..``, no odd characters."""
    if (not path or path.startswith("/") or "\\" in path or "\0" in path
            or "\t" in path or "\n" in path or "\r" in path):
        raise ManifestError(f"unsafe path in manifest: {path!r}")
    if any(part in ("", ".", "..") for part in path.split("/")):
        raise ManifestError(f"unsafe path in manifest: {path!r}")
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ── measuring a tree ───────────────────────────────────────────────────────


def walk(app_root: Path) -> Iterable[tuple[str, os.stat_result, Path]]:
    """Every file and symlink under the bundle (relative posix path, lstat, absolute path).

    Symlinks are reported, never followed - a symlinked directory is one entry, not its contents.
    The manifest directory and ``.DS_Store`` are skipped.
    """
    root = str(app_root)
    skip = (root.rstrip("/") + "/" + DIR_REL)
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        if directory == skip:
            dirnames[:] = []
            continue
        for name in list(dirnames):
            full = os.path.join(directory, name)
            if os.path.islink(full):
                dirnames.remove(name)
                filenames.append(name)
        for name in filenames:
            if name in IGNORED_NAMES:
                continue
            full = os.path.join(directory, name)
            info = os.lstat(full)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                raise ManifestError(f"{rel} is neither a file nor a symlink")
            yield rel, info, Path(full)


def scan(app_root: Path, *, reuse: Mapping[str, Entry] | None = None) -> dict[str, Entry]:
    """Measure a bundle. ``reuse`` lets a caller skip hashing a file it already knows by size."""
    entries: dict[str, Entry] = {}
    for rel, info, full in walk(app_root):
        safe_relpath(rel)
        if stat.S_ISLNK(info.st_mode):
            target = os.readlink(full)
            if not target or "\0" in target or "\t" in target or "\n" in target:
                raise ManifestError(f"{rel} is a symlink whose target cannot be recorded")
            entries[rel] = ("l", False, 0, target)
            continue
        executable = bool(info.st_mode & stat.S_IXUSR)
        known = (reuse or {}).get(rel)
        digest = known[3] if known and known[0] == "f" and known[2] == info.st_size \
            else sha256_file(full)
        entries[rel] = ("f", executable, info.st_size, digest)
    return entries


# ── the wire form ──────────────────────────────────────────────────────────


def by_group(entries: Mapping[str, Entry]) -> dict[str, dict[str, Entry]]:
    groups: dict[str, dict[str, Entry]] = {"core": {}}
    for path, entry in entries.items():
        groups.setdefault(group_of(path), {})[path] = entry
    return groups


def serialize(entries: Mapping[str, Entry]) -> bytes:
    lines = []
    for path in sorted(entries, key=lambda item: item.encode("utf-8")):
        kind, executable, size, digest = entries[path]
        lines.append(f"{kind}\t{int(executable)}\t{size}\t{digest}\t{path}\n")
    return "".join(lines).encode("utf-8")


def parse(data: bytes) -> dict[str, Entry]:
    """Strict: any malformed, duplicated, unsorted or unsafe line refuses the whole group."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError("manifest group is not UTF-8") from exc
    entries: dict[str, Entry] = {}
    previous: bytes | None = None
    for number, line in enumerate(text.splitlines(), 1):
        fields = line.split("\t")
        if len(fields) != 5:
            raise ManifestError(f"manifest line {number} does not have 5 fields")
        kind, x, size, digest, path = fields
        safe_relpath(path)
        key = path.encode("utf-8")
        if previous is not None and key <= previous:
            raise ManifestError(f"manifest line {number} is duplicated or out of order")
        previous = key
        if x not in ("0", "1") or not size.isascii() or not size.isdigit():
            raise ManifestError(f"manifest line {number} has a bad executable bit or size")
        if kind == "f":
            if not _HEX64.match(digest):
                raise ManifestError(f"manifest line {number} has no sha256")
            entries[path] = ("f", x == "1", int(size), digest)
        elif kind == "l":
            if not digest or int(size) != 0 or x != "0":
                raise ManifestError(f"manifest line {number} is a malformed symlink")
            entries[path] = ("l", False, 0, digest)
        else:
            raise ManifestError(f"manifest line {number} has unknown type {kind!r}")
    return entries


def files_digest(group_shas: Mapping[str, str]) -> str:
    """The one number a descriptor signs: it commits to every group, and so to every file."""
    body = "".join(f"{name}\0{group_shas[name]}\n" for name in sorted(group_shas))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def gzip_bytes(data: bytes) -> bytes:
    return gzip.compress(data, compresslevel=9, mtime=0)


def build(entries: Mapping[str, Entry]) -> tuple[dict, dict[str, bytes]]:
    """``(index, {group: uncompressed tsv})`` for measured entries. Deterministic."""
    tsv: dict[str, bytes] = {}
    groups: dict[str, dict] = {}
    for name, members in sorted(by_group(entries).items()):
        data = serialize(members)
        tsv[name] = data
        groups[name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "files": len(members),
            "bytes": sum(item[2] for item in members.values()),
            "prefix": group_prefix(name),
        }
    index = {
        "schema": SCHEMA,
        "files_sha256": files_digest({name: item["sha256"] for name, item in groups.items()}),
        "files_count": sum(item["files"] for item in groups.values()),
        "files_bytes": sum(item["bytes"] for item in groups.values()),
        "groups": groups,
    }
    return index, tsv


def index_bytes(index: Mapping) -> bytes:
    return (json.dumps(index, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write(app_root: Path, entries: Mapping[str, Entry] | None = None) -> dict:
    """Write ``file-manifest/`` into the bundle and return the index."""
    index, tsv = build(scan(app_root) if entries is None else entries)
    directory = app_root / DIR_REL
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in tsv.items():
        (directory / f"{name}.tsv.gz").write_bytes(gzip_bytes(data))
    (directory / INDEX_NAME).write_bytes(index_bytes(index))
    return index


def check_index(value: object) -> dict:
    """Validate an index and recompute ``files_sha256`` from its groups; returns it."""
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ManifestError("file manifest index has the wrong schema")
    groups = value.get("groups")
    if not isinstance(groups, dict) or "core" not in groups:
        raise ManifestError("file manifest index has no core group")
    shas: dict[str, str] = {}
    total_files = total_bytes = 0
    for name, item in groups.items():
        if not _GROUP.match(str(name)) or not isinstance(item, dict):
            raise ManifestError(f"file manifest group name {name!r} is not allowed")
        sha, files, size = item.get("sha256"), item.get("files"), item.get("bytes")
        if not isinstance(sha, str) or not _HEX64.match(sha):
            raise ManifestError(f"group {name} has no sha256")
        if not all(isinstance(number, int) and not isinstance(number, bool) and number >= 0
                   for number in (files, size)):
            raise ManifestError(f"group {name} has bad counts")
        shas[name] = sha
        total_files += files
        total_bytes += size
    if (value.get("files_sha256") != files_digest(shas)
            or value.get("files_count") != total_files
            or value.get("files_bytes") != total_bytes):
        raise ManifestError("file manifest index does not match its own groups")
    return value


def check_group(index: Mapping, name: str, tsv: bytes) -> dict[str, Entry]:
    """Parse one group's uncompressed TSV and prove it is the one the index names."""
    described = index["groups"].get(name)
    if described is None:
        raise ManifestError(f"the manifest has no group {name}")
    if hashlib.sha256(tsv).hexdigest() != described["sha256"]:
        raise ManifestError(f"group {name} does not match the sha256 the index names")
    entries = parse(tsv)
    if (len(entries) != described["files"]
            or sum(item[2] for item in entries.values()) != described["bytes"]):
        raise ManifestError(f"group {name} does not match the counts the index names")
    for path in entries:
        if group_of(path) != name:
            raise ManifestError(f"{path} does not belong in group {name}")
    return entries


def gunzip_limited(data: bytes, limit: int = 256 * 1024 * 1024) -> bytes:
    """Decompress a group, refusing a bomb: a real group is tens of MB at most."""
    import zlib
    try:
        out = zlib.decompressobj(wbits=31).decompress(data, limit + 1)
    except zlib.error as exc:
        raise ManifestError("manifest group is not valid gzip") from exc
    if len(out) > limit:
        raise ManifestError("manifest group is implausibly large")
    return out


def load_dir(directory: Path) -> tuple[dict, dict[str, dict[str, Entry]]]:
    """Read and verify a stored manifest directory. Raises ``ManifestError`` on any fault."""
    try:
        index = check_index(json.loads((directory / INDEX_NAME).read_text(encoding="utf-8")))
        groups = {
            name: check_group(index, name,
                              gunzip_limited((directory / f"{name}.tsv.gz").read_bytes()))
            for name in index["groups"]
        }
    except (OSError, ValueError) as exc:
        if isinstance(exc, ManifestError):
            raise
        raise ManifestError(f"the manifest in {directory} cannot be read: {exc}") from exc
    return index, groups


def flatten(groups: Mapping[str, Mapping[str, Entry]]) -> dict[str, Entry]:
    return {path: entry for members in groups.values() for path, entry in members.items()}
