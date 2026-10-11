"""Verified, portable model files. Generation eligibility belongs to the Hub.

This module uses only Python's standard library and never imports model code.
Run with --help for the build/operator CLI; native controls are a later phase.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
import uuid


class PackageError(ValueError):
    pass


# One relationship, two spellings — and both were shipped. `requires_packages` is what this
# module validates and resolves (preflight, verify, download, capture); `dependencies` was the
# first spelling, used by `chatterbox-{4,8}bit`. Reading only one of them meant a declared
# companion was silently ignored: the package that needed it verified as `files_verified` with
# the companion absent, and the engine fetched the missing tokenizer at generation time -
# exactly the offline failure the relationship exists to prevent. Both spellings are read
# through `required_packages` below, and `validate_manifest` now refuses the legacy one so the
# migrated catalog cannot drift back into two shapes.
COMPANION_FIELD = 'requires_packages'
LEGACY_COMPANION_FIELD = 'dependencies'


def _companion_list(manifest, field):
    value = manifest.get(field)
    if value is None:
        return []
    if not isinstance(value, list):
        raise PackageError(f'Invalid companion package requirements in {field!r}')
    return value


def required_packages(manifest) -> list[str]:
    """The companion package ids this manifest declares, under either spelling it ships.

    The single reader of the companion relationship. Manifest order is preserved and duplicates
    across the two fields collapse, so a manifest mid-migration cannot declare one companion
    twice.

    This stays tolerant of the legacy spelling on purpose. `validate_manifest` refuses a
    manifest that still declares companions in `dependencies`, so nothing this project ships
    can carry one; but this function also reads rows that arrived from elsewhere - an older
    kit, a hand-written manifest - and a package whose declared companion was dropped because
    of the field it was written in is exactly the offline failure the relationship prevents.
    """
    if not isinstance(manifest, dict):
        raise PackageError('A manifest is required to read companion packages')
    ids: list[str] = []
    for field in (COMPANION_FIELD, LEGACY_COMPANION_FIELD):
        for dependency in _companion_list(manifest, field):
            if dependency not in ids:
                ids.append(dependency)
    return ids


def production_allowed(manifest) -> bool:
    """Whether this manifest may serve production work.

    Existing manifests predate the explicit gate and keep their established policy. A present
    value must be the literal boolean ``True``; malformed metadata therefore fails closed even
    before the manifest validator reports the bad field.
    """
    return 'production_allowed' not in manifest or manifest.get('production_allowed') is True


def _reject_legacy_companions(manifest) -> None:
    """Refuse a manifest that declares its companions under the legacy spelling.

    Both spellings were read for one release while the catalog was migrated. Tolerating both
    forever is how a second spelling survives: the field no validator rejects stays writable,
    and the next model's author has two shapes to choose from and no way to know which one the
    readers use. The shipped catalog is migrated - `chatterbox-{4,8}bit`'s `s3tokenizerv2` was
    the last - so this is now an error naming the field to move, not a warning.
    """
    legacy = _companion_list(manifest, LEGACY_COMPANION_FIELD)
    if not legacy:
        return
    raise PackageError(
        f"Manifest {manifest.get('package_id')!r} declares its companion packages "
        f"{legacy!r} in {LEGACY_COMPANION_FIELD!r}; the one companion field is "
        f"{COMPANION_FIELD!r}. Move the list to {COMPANION_FIELD!r} in the manifest so every "
        f"reader resolves the same relationship.")


def _relative(value):
    if (not isinstance(value, str) or not value or '\\' in value or '\0' in value
            or any(p in ('', '.', '..', '.studiohub') for p in value.split('/'))
            or PurePosixPath(value).is_absolute()):
        raise PackageError(f'Unsafe package path: {value!r}')
    return value


def _url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError()
        if parsed.fragment:
            raise ValueError()
        parsed.port
    except (TypeError, ValueError):
        raise PackageError('Model sources must be credential-free HTTPS URLs') from None
    return value


def peer_base(value):
    """The files prefix of a LAN share (``…/api/hub/model-share/<token>/files``) from another Mac.

    Plain HTTP is allowed here and only here: a peer is reached over the LAN or Tailscale, and every
    byte it sends is checked against the manifest's pinned sha256 exactly like an internet download.
    """
    try:
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError()
        parsed.port
    except (TypeError, ValueError):
        raise PackageError('A LAN source must be a credential-free http(s) share URL') from None
    return value.rstrip('/')


# Every engine a manifest may declare. The Hub keeps the matching worker/modality map in
# `_ENGINE_ROUTE` (`components/hub/standalone.py`); the two are one vocabulary in two files, so
# they are named here and cross-checked by a test rather than left to drift. When this tuple and
# that mapping disagreed, `chat-mlx` reached a manifest with no worker: `/api/hub/model-packages`
# raised `KeyError` and returned 500, the native refresh read that as "Hub unreachable", and every
# model picker on every Mac went blank.
ENGINES = ('image-mflux', 'image-mlx-serve', 'voice-mlx', 'voice-vits', 'subtitle-mlx',
           'music-mlx', 'music-yue2', 'chat-mlx', 'video-ltx', 'embed-mlx',
           'vision-mlx')
ENGINE_HELP = ("add it to _ENGINE_ROUTE in components/hub/standalone.py and to the engine "
               "allowlist in components/model_packages.py")

# The `subtitle` capabilities block (`components/subtitle/**`) — one manifest contract, validated
# here so a subtitle package cannot be published with capabilities nobody declared.
SUBTITLE_BLOCK = 'subtitle'
SUBTITLE_FAMILY_FIELD = 'family'
SUBTITLE_LANGUAGES_FIELD = 'languages'
SUBTITLE_FLAG_FIELDS = ('segment_timestamps', 'word_timestamps', 'long_form')
SUBTITLE_FIELDS = (SUBTITLE_FAMILY_FIELD, SUBTITLE_LANGUAGES_FIELD, *SUBTITLE_FLAG_FIELDS)
SUBTITLE_RUNTIME_FIELD = 'runtime_seconds'


def _runtime_seconds_problem(value):
    """Why a `subtitle.runtime_seconds` block is not publishable, or None when it is.

    The lane contract publishes what one item of this lane costs, and only where it has been
    measured (`docs/lane-execution-contract.md`). A guessed number is how a scheduler sets a
    deadline that fails on use, so the block must carry the measurement and say so; there is no
    shape here for an estimate.

    `measured` is always a list of rows — one per configuration that was run — so a lane whose
    cost depends on a request parameter publishes the same shape as one whose cost does not. Each
    row carries `seconds` and whatever names the configuration that produced it, so a scheduler
    can pick the row matching the request it is about to send.
    """
    if not isinstance(value, dict):
        return f'it is {value!r}, not an object'
    if value.get('evidence') != 'measured':
        return ("'evidence' must be 'measured' — a runtime that was not measured is not published "
                "at all, so there is no shape for an estimate here")
    rows = value.get('measured')
    if not isinstance(rows, list) or not rows:
        return f"'measured' is {rows!r}, not a non-empty list of measured rows"
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return f"'measured[{index}]' is {row!r}, not an object"
        seconds = row.get('seconds')
        if type(seconds) not in (int, float) or isinstance(seconds, bool) or seconds <= 0:
            return (f"'measured[{index}].seconds' is {seconds!r}, not a positive number of "
                    f"seconds")
        if len(row) < 2:
            return (f"'measured[{index}]' names no configuration: beside 'seconds' it must carry "
                    f"what produced the number (the input, the settings, the model load), so a "
                    f"scheduler can pick the row that matches the request it is about to send")
    if not isinstance(value.get('measured_on'), str) or not value['measured_on'].strip():
        return ("'measured_on' must name the host, engine and quantization the rows were measured "
                "on, so the number is attributable")
    if 'source' in value and (not isinstance(value['source'], str) or not value['source'].strip()):
        return "'source' is present but names nothing; name where the measurement is recorded"
    return None


def validate_subtitle_capabilities(manifest):
    """The block a `subtitle-mlx` manifest must carry, validated in one place.

    The Subtitle worker used to publish `family: 'whisper'`, `languages: 'Multilingual'` and three
    `True` flags for every package it loaded, because no shipped manifest declared any of them.
    Nothing was reading the truth, so nothing could be wrong: a non-Whisper subtitle engine was
    labelled Whisper, and a model that cannot produce word timestamps was advertised as able to.
    The block below is the one place a subtitle package states what it can do, and the reader
    publishes exactly it.

    Only a subtitle manifest is required to carry it; a manifest for any other engine is not
    forced to, and a subtitle block on another engine's manifest is not read.
    """
    package_id = manifest.get('package_id')
    where = f'Manifest {package_id!r}'
    block = manifest.get(SUBTITLE_BLOCK)
    if not isinstance(block, dict):
        raise PackageError(
            f"{where} declares engine 'subtitle-mlx' but carries no {SUBTITLE_BLOCK!r} block. "
            f"Add \"{SUBTITLE_BLOCK}\": {{{', '.join(repr(field) for field in SUBTITLE_FIELDS)}}} "
            f"to its manifest (model-manifests/{package_id}.json) with what the model's own config "
            f"and the engine's decode path actually do; the Subtitle worker publishes only what "
            f"this block says and must never fall back to a family or a capability of its own.")
    missing = [field for field in SUBTITLE_FIELDS if field not in block]
    if missing:
        raise PackageError(
            f"{where} has an incomplete {SUBTITLE_BLOCK!r} block: missing "
            f"{', '.join(repr(field) for field in missing)}. Every field is published, so an "
            f"absent one would be published as something nobody declared. Complete the block in "
            f"model-manifests/{package_id}.json.")
    family = block[SUBTITLE_FAMILY_FIELD]
    if not isinstance(family, str) or not family.strip():
        raise PackageError(
            f"{where} has an invalid {SUBTITLE_BLOCK}.{SUBTITLE_FAMILY_FIELD}: "
            f"{family!r}. It names the model family this package belongs to "
            f"(model-manifests/{package_id}.json).")
    languages = block[SUBTITLE_LANGUAGES_FIELD]
    if (not isinstance(languages, list) or not languages
            or any(not isinstance(item, str) or not item.strip() for item in languages)):
        raise PackageError(
            f"{where} has an invalid {SUBTITLE_BLOCK}.{SUBTITLE_LANGUAGES_FIELD}: "
            f"{languages!r}. It is a non-empty list of language names or tags, and it is published "
            f"verbatim (model-manifests/{package_id}.json).")
    for field in SUBTITLE_FLAG_FIELDS:
        if type(block[field]) is not bool:
            raise PackageError(
                f"{where} has an invalid {SUBTITLE_BLOCK}.{field}: {block[field]!r}. It is a "
                f"boolean that is published as-is; a consumer must not have to guess whether a "
                f"missing or non-boolean value means True (model-manifests/{package_id}.json).")
    runtime = block.get(SUBTITLE_RUNTIME_FIELD)
    if runtime is not None:
        problem = _runtime_seconds_problem(runtime)
        if problem is not None:
            raise PackageError(
                f"{where} has an invalid {SUBTITLE_BLOCK}.{SUBTITLE_RUNTIME_FIELD}: {problem} "
                f"(model-manifests/{package_id}.json).")
    return block


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 1:
        raise PackageError('Unsupported model manifest schema')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,99}', str(manifest.get('package_id', ''))):
        raise PackageError('Invalid package_id')
    if 'production_allowed' in manifest and type(manifest['production_allowed']) is not bool:
        raise PackageError(
            f"Manifest {manifest.get('package_id')!r} has a non-boolean 'production_allowed': "
            f"{manifest['production_allowed']!r}")
    if not re.fullmatch(r'[0-9a-f]{40}', str(manifest.get('revision', ''))):
        raise PackageError('A full immutable model revision is required')
    # Name the engine that has no worker, and both files that carry the vocabulary. A bare
    # 'Unsupported model engine' left an operator (and the 0.6.57 incident) with nothing to act
    # on: the message arrived as a `PackageError` inside the packages route, and the whole
    # catalog is only as useful as the one manifest it cannot describe.
    if manifest.get('engine') not in ENGINES:
        raise PackageError(
            f"Manifest {manifest.get('package_id')!r} declares engine "
            f"{manifest.get('engine')!r}, which this build does not know; {ENGINE_HELP}")
    if manifest.get('engine') == 'subtitle-mlx':
        validate_subtitle_capabilities(manifest)
    if manifest.get('engine') == 'subtitle-mlx' and 'default' in manifest and type(manifest['default']) is not bool:
        raise PackageError(
            f"Manifest {manifest.get('package_id')!r} has a non-boolean 'default': "
            f"{manifest['default']!r}. A manifest declares the default subtitle model with "
            f"\"default\": true, or says nothing (model-manifests/{manifest.get('package_id')}.json).")
    if not isinstance(manifest.get('model_id'), str) or not manifest['model_id']:
        raise PackageError('Missing model_id')
    if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', str(manifest.get('minimum_macos', ''))):
        raise PackageError('Missing minimum_macos candidate')
    entries = manifest.get('files')
    if not isinstance(entries, list) or not entries:
        raise PackageError('A complete file manifest is required')
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise PackageError('Invalid file entry')
        path = _relative(entry.get('path'))
        # macOS commonly uses a case-insensitive volume.
        if path.casefold() in seen:
            raise PackageError(f'Duplicate package path: {path}')
        seen.add(path.casefold())
        if 'source_path' in entry:
            _relative(entry['source_path'])
        if type(entry.get('size')) is not int or entry['size'] < 0:
            raise PackageError(f'Invalid file size: {path}')
        if not re.fullmatch(r'[0-9a-f]{64}', str(entry.get('sha256', ''))):
            raise PackageError(f'Missing SHA-256: {path}')
        urls = entry.get('urls', [])
        if not isinstance(urls, list):
            raise PackageError(f'Invalid sources: {path}')
        for url in urls:
            _url(url)
    for path in seen:
        if any('/'.join(path.split('/')[:i]) in seen for i in range(1, len(path.split('/')))):
            raise PackageError(f'File/directory collision: {path}')
    dependencies = manifest.get('requires_packages', [])
    if not isinstance(dependencies, list) or any(
            not isinstance(dep, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,99}', dep)
            or dep == manifest['package_id'] for dep in dependencies):
        raise PackageError('Invalid companion package requirements')
    # The legacy spelling is refused outright, so a manifest cannot hide a malformed or a
    # forgotten companion list in a field no reader resolves.
    _reject_legacy_companions(manifest)
    return manifest


def manifest_digest(manifest):
    validate_manifest(manifest)
    identity = {k: manifest[k] for k in ('schema_version', 'package_id', 'model_id',
                                        'revision', 'engine', 'minimum_macos')}
    # The canonical field, which `validate_manifest` above has just guaranteed is the only
    # spelling this manifest carries. Migrating `chatterbox-{4,8}bit` off the legacy field
    # therefore changes that package's digest, and its receipt is re-verified once - correct,
    # because the receipt being replaced was written by code that ignored the companion
    # relationship altogether.
    identity['requires_packages'] = sorted(manifest.get(COMPANION_FIELD, []))
    identity['files'] = sorted([{k: entry[k] for k in ('path', 'size', 'sha256', 'source_path')
                                 if k in entry} for entry in manifest['files']], key=lambda e: e['path'])
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _library(path):
    try:
        root = Path(path).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise OSError()
    except OSError:
        raise PackageError('Selected model library is unavailable; reconnect or select its volume') from None
    state = root / '.studiohub'
    if state.is_symlink() or (state.exists() and not state.is_dir()):
        raise PackageError('Unsafe model library state directory')
    if state.exists() and (state.stat().st_uid != os.geteuid() or state.stat().st_mode & 0o077):
        raise PackageError('Model library state must be owner-only (0700)')
    return root


@contextmanager
def _directory(root, relative='', create=False):
    """Anchor writes to opened directories, even if a pathname is swapped."""
    descriptors = []
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(fd)
        for part in filter(None, relative.split('/')):
            if part in ('.', '..'):
                raise PackageError('Unsafe directory component')
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            descriptors.append(fd)
        yield fd
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _mkdir(root, relative):
    try:
        with _directory(root, relative, create=True):
            pass
    except OSError:
        raise PackageError('Unsafe or unavailable model directory') from None
    return root / relative


@contextmanager
def _lock(root):
    with _directory(root, '.studiohub', create=True) as state_fd:
        fd = os.open('import.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=state_fd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise PackageError('Unsafe model import lock')
            # ponytail: one importer per library; parallel transfers only if measured necessary.
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PackageError('Another model import is active for this library') from None
            yield
        finally:
            os.close(fd)


def _index(root):
    path = root / '.studiohub/installed.json'
    if path.is_symlink():
        raise PackageError('Unsafe model index')
    if not path.exists():
        return {}
    try:
        with _open_regular(root, '.studiohub/installed.json') as stream:
            value = json.load(stream)
        if not isinstance(value, dict) or any(not isinstance(record, dict) for record in value.values()):
            raise ValueError()
        return value
    except (OSError, ValueError):
        raise PackageError('Model index is invalid; no installed files were changed') from None


@contextmanager
def _open_regular(root, relative):
    """Do not follow intermediate symlinks, and never block on a FIFO/device."""
    descriptors = []
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(fd)
        parts = relative.split('/')
        for part in parts[:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            descriptors.append(fd)
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o444:
                raise PackageError(f'Unreadable regular file required: {relative}')
            yield stream
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


# What one file's receipt records about the copy on disk.
#
# These two survive a copy between filesystems, which is the case that matters: a verified
# library is copied onto an SSD and then onto each Mac. Measured, not assumed - after a copy,
# `st_size` and `st_mtime_ns` are identical while `st_ctime_ns`, `st_ino` and `st_dev` are not.
#
# Every timestamp was tried and every one of them broke the same way. On a copy between two
# APFS volumes - which is how the kit travels - `st_ino` and `st_dev` change outright, and
# `st_mtime_ns` loses its sub-microsecond digits (measured: 1789464238184185311 becomes
# 1789464238000000000). The result on the first machine that was not the build machine was
#
#     File changed; verify again
#
# for all twelve packages of a correctly copied 29 GB library. A receipt that accuses a file of
# changing when only its metadata moved teaches the operator to ignore it, which is worse than
# having no receipt at all.
#
# So the cheap path records size and nothing else. Size catches truncation and a half-finished
# copy - the failure that actually happens - while genuine integrity is a hash: `verify_installed`
# rehashes every byte, the Hub hashes every artifact it serves, and a library that arrived from
# another machine is re-verified once on arrival rather than trusted on faith. See
# `library_identity` in `preflight`.
# Only fields that survive being copied, because a receipt ships with the library.
#
# `ctime` was the last one to go. It changes on every copy - measured again while shipping a
# receipt that had just been written here and rsynced to the SSD - so a receipt written on one
# machine can never match the same files on another, and every package reads as changed. `size`
# and `mtime` are the two that survive, measured rather than assumed.
_STAMP_FIELDS = ('st_size', 'mtime_s')


def _stamp(info):
    """What the receipt records about one file, in whole seconds.

    Whole seconds on purpose. Nanosecond timestamps do not survive the copy the kit ships:
    measured between two APFS volumes, `st_mtime_ns` came back with its last three digits
    (1789464238184185311 -> 1789464238000000000), and `st_ino`/`st_dev` change outright. A
    receipt keyed on any of that accused every file of changing on the first machine that was
    not the build machine.

    Seconds are coarse enough to survive the copy and fine enough to catch a file rewritten in
    place later - and `st_size` catches truncation, which is the failure that actually happens
    to a copied library. Byte-level integrity stays a hash: `verify_installed` rehashes
    everything, and the Hub hashes every artifact it serves.
    """
    return {'st_size': getattr(info, 'st_size'),
            'mtime_s': getattr(info, 'st_mtime_ns') // 1_000_000_000}


def _file_path(folder, entry):
    target = folder / entry['path']
    if not target.exists() and not target.is_symlink() and entry.get('source_path'):
        return entry['source_path']
    return entry['path']


@contextmanager
def _source_stream(folder, entry):
    relative = _file_path(folder, entry)
    path = folder / relative
    if path.is_symlink():
        # Imported HF snapshots may reference sibling blobs; installed packages never do.
        try:
            resolved = path.resolve(strict=True)
            allowed = folder
            if folder.parent.name == 'snapshots':
                allowed = folder.parent.parent / 'blobs'
            resolved.relative_to(allowed.resolve(strict=True))
        except (OSError, ValueError):
            raise PackageError(f'Broken or external source link: {entry["path"]}') from None
        with _open_regular(allowed.resolve(), resolved.relative_to(allowed.resolve()).as_posix()) as stream:
            yield stream
    else:
        with _open_regular(folder, relative) as stream:
            yield stream


def _check_stream(stream, entry, destination=None):
    digest = hashlib.sha256()
    size = 0
    first = True
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        if first and chunk.startswith(b'version https://git-lfs.github.com/spec/'):
            raise PackageError(f'Git LFS pointer instead of model data: {entry["path"]}')
        first = False
        size += len(chunk)
        if size > entry['size']:
            raise PackageError(f'Unexpected file size: {entry["path"]}')
        digest.update(chunk)
        if destination is not None:
            destination.write(chunk)
    # The failure says which check failed and what was measured. "Incomplete or invalid" left the
    # operator with a file name and no reason, which is the shape of message that gets ignored
    # rather than acted on - and a size that differs and a hash that differs are different
    # repairs (a truncated copy against a file that is simply not this model's).
    if size != entry['size']:
        raise PackageError(
            f'Incomplete model file: {entry["path"]} is {size} bytes, not the {entry["size"]} '
            f'bytes the manifest records')
    measured = digest.hexdigest()
    if measured != entry['sha256']:
        raise PackageError(
            f'Model file does not match its manifest: {entry["path"]} hashes to '
            f'sha256:{measured}, not the sha256:{entry["sha256"]} the manifest records')


# ── Companion packages must also reach the engine the way the engine reads them ────────────
#
# A companion's bytes reach the library at `<library>/<package_id>/<revision>/...`, but several
# engines never look there. They call `hf_hub_download` / `snapshot_download`, which resolve
# through the Hugging Face cache layout under the hub's own cache root - `packaging/voice_worker.py`
# launches every Voice worker with `HF_HUB_CACHE=<library>/hub`, and the Subtitle worker does the
# same. Measured on the Pocket TTS 4-bit path (`mlx-community/pocket-tts-4bit`): the model's own
# `config.json` names its sentencepiece tokenizer as
# `hf://kyutai/pocket-tts-without-voice-cloning/tokenizer.model@d4fdd22a...`, and mlx-audio's
# `pocket_tts/utils.py:download_if_necessary` hands that straight to `hf_hub_download`. On a Mac
# whose cache was populated by a download that works; on a kit node that only received the copied
# package directories the lookup finds nothing and the failure surfaces at generation time - the
# exact offline failure the companion relationship was introduced to prevent.
#
# So an installed companion is mirrored into that layout, derived entirely from manifest data:
# `source.layout` names each publisher repo, revision and destination; `files[].source_path` (or
# the file's path below its destination) names its path inside that repository; `files[].path`
# names the installed file the bytes come from. No repository, family or file name is listed here,
# so a new companion package works with no code change.
#
# `refs/<default>` is written too, and only when it is absent. huggingface_hub 1.31.0 (the version
# the workers ship) resolves a 40-hex revision straight to `snapshots/<revision>/<file>` with no
# ref and no network; but an engine that names no revision resolves through `refs/main` first -
# chatterbox asks `snapshot_download(repo_id="mlx-community/S3TokenizerV2")`, and without that ref
# the offline lookup raises. The ref *name* is huggingface_hub's default revision, not a manifest
# field: every revision in this catalog is pinned from that cache's `refs/main`
# (`qualification/generate_manifests_from_cache.py:86-92`), so pointing `main` at the manifest's
# verified commit is what the manifest already records.
HF_HUB_DIRNAME = 'hub'
HF_DEFAULT_REF = 'main'
HF_REPO_FOLDER_PREFIX = 'models--'
COMPANION_CACHE_STATE = 'companion_cache_missing'


def _hf_repo_folder(repo):
    return HF_REPO_FOLDER_PREFIX + repo.replace('/', '--')


def _hf_cache_relative(repo, revision, publish_path):
    return '/'.join((HF_HUB_DIRNAME, _hf_repo_folder(repo), 'snapshots', revision, publish_path))


def layout_locations(manifest):
    """Every publisher root the manifest's own ``source.layout`` records.

    `model` is the model tree; `companions[]` are the extra trees a package copies in beside it
    (the Whisper processor repositories, for instance). Each carries the destination it was
    installed to, the repository it came from and that repository's revision.
    """
    source = manifest.get('source')
    layout = source.get('layout') if isinstance(source, dict) else None
    if not isinstance(layout, dict):
        return []
    locations = []
    if isinstance(layout.get('model'), dict):
        locations.append(layout['model'])
    if isinstance(layout.get('companions'), list):
        locations.extend(item for item in layout['companions'] if isinstance(item, dict))
    return locations


def publisher_location(manifest, entry):
    """Where one installed file of a manifest lives in its publisher's repository.

    Returns ``(repo, revision, path inside that repository)``. Raises :class:`PackageError`
    naming the manifest, the file and the field that does not say: a file this cannot place is a
    file the engine will fetch over the network, so it is reported rather than skipped.
    """
    package_id = manifest.get('package_id')
    installed = entry.get('path')
    matches = []
    for location in layout_locations(manifest):
        destination = location.get('destination')
        if not isinstance(destination, str) or not destination.strip():
            continue
        destination = destination.strip('/')
        if installed == destination:
            remainder = ''
        elif destination and installed.startswith(destination + '/'):
            remainder = installed[len(destination) + 1:]
        else:
            continue
        matches.append((len(destination), location, remainder))
    if not matches:
        raise PackageError(
            f"Manifest {package_id!r} does not say where its file {installed!r} lives in a "
            f"publisher repository: 'source.layout' has no destination containing that path. "
            f"Add the missing destination/repo/revision to 'source.layout' in "
            f"model-manifests/{package_id}.json so the file can be placed where the engine reads it.")
    _, location, remainder = max(matches, key=lambda match: match[0])
    repo = location.get('repo')
    revision = location.get('revision')
    if not isinstance(repo, str) or not repo.strip() or '/' not in repo.strip():
        raise PackageError(
            f"Manifest {package_id!r} has no publisher repository for {installed!r}: "
            f"'source.layout' names {repo!r}. It is the Hugging Face repository id the file came "
            f"from; set 'source.layout[...].repo' in model-manifests/{package_id}.json.")
    if not re.fullmatch(r'[0-9a-f]{40}', str(revision or '')):
        raise PackageError(
            f"Manifest {package_id!r} has no immutable publisher revision for {installed!r}: "
            f"'source.layout' names {revision!r}. Set 'source.layout[...].revision' in "
            f"model-manifests/{package_id}.json.")
    publish_path = entry.get('source_path') or remainder
    if not isinstance(publish_path, str) or not publish_path.strip():
        raise PackageError(
            f"Manifest {package_id!r} does not say where {installed!r} lives inside "
            f"{repo}@{revision}: neither 'source_path' nor its path below the layout destination "
            f"names one. Set 'source_path' in model-manifests/{package_id}.json.")
    return repo.strip(), str(revision), _relative(publish_path.strip('/'))


def _companion_plan(root, manifest, catalog):
    """What every installed companion file should become, derived from the manifests alone.

    Returns ``(plan, problems)``: ``plan`` maps a library-relative cache path to the record needed
    to write it, and ``problems`` names everything that could not be placed. Shared by the cheap
    status check and the writer, so what is checked is exactly what is written.
    """
    plan: dict[str, dict] = {}
    problems: list[str] = []
    catalog = catalog or {}
    try:
        index = _index(root)
    except PackageError as error:
        return plan, [str(error)]
    for dependency in required_packages(manifest):
        companion = catalog.get(dependency)
        if not isinstance(companion, dict) or companion.get('package_id') != dependency:
            problems.append(
                f'companion package {dependency!r} has no manifest in the catalog this command '
                f'was given, so the publisher path of its files cannot be read; its files will '
                f'not be reachable by the engine until the package is verified with the catalog')
            continue
        record = index.get(dependency)
        if not isinstance(record, dict):
            problems.append(
                f'companion package {dependency!r} is not installed in this library, so there is '
                f'nothing to place in the engine cache')
            continue
        try:
            folder = _relative(record.get('folder') or '')
        except PackageError:
            problems.append(
                f'companion package {dependency!r} has no usable installed folder in the receipt')
            continue
        saved_files = record.get('files') if isinstance(record.get('files'), dict) else {}
        for entry in companion.get('files') or []:
            if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
                problems.append(f'companion package {dependency!r} has an unusable file entry')
                continue
            saved = saved_files.get(entry['path']) or {}
            installed = saved.get('relative') or entry['path']
            try:
                source = _relative(installed)
                repo, revision, publish_path = publisher_location(companion, entry)
            except PackageError as error:
                problems.append(str(error))
                continue
            target = _hf_cache_relative(repo, revision, publish_path)
            plan[target] = {'package_id': dependency, 'source': folder + '/' + source,
                            'entry': entry, 'repo': repo, 'revision': revision}
    return plan, problems


def _target_matches(root, target, entry):
    """Whether an existing cache entry already holds exactly this file's bytes."""
    path = root / target
    try:
        info = path.stat()  # follows a real Hugging Face snapshot symlink into blobs/
        if not stat.S_ISREG(info.st_mode) or info.st_size != entry['size']:
            return False
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest() == entry['sha256']
    except OSError:
        return False


def companion_cache_status(manifest, library, *, catalog=None):
    """Whether every installed companion is where the engine's lookup will find it.

    Stat-only on purpose: this is the cheap half of :func:`materialize_companions`, shared with
    `preflight`, so a package can be described without reading bytes. A file that is present at
    the right size counts; byte identity is the writer's job.
    """
    root = _library(library)
    if not required_packages(manifest):
        return {'state': 'not_required', 'reason': None, 'packages': {}, 'files': {}}
    plan, problems = _companion_plan(root, manifest, catalog)
    files = {}
    missing = []
    for target in sorted(plan):
        entry = plan[target]['entry']
        try:
            info = (root / target).stat()
            present = stat.S_ISREG(info.st_mode) and info.st_size == entry['size']
        except OSError:
            present = False
        files[target] = 'present' if present else 'missing'
        if not present:
            missing.append(target)
            problems.append(
                f"companion file {plan[target]['entry']['path']} of "
                f"{plan[target]['package_id']!r} is not in this library's Hugging Face cache at "
                f"<library>/{target}, so the engine would have to fetch it at generation time")
    if problems:
        return {'state': 'missing', 'reason': '; '.join(problems), 'files': files,
                'missing': missing}
    return {'state': 'ready', 'reason': None, 'files': files, 'missing': []}


def _write_cache_file(root, target, plan_entry):
    """Place one file at one cache path: hard link, else copy. Never overwrites what is right."""
    entry = plan_entry['entry']
    if os.path.lexists(root / target) and _target_matches(root, target, entry):
        return 'present'
    parent, _, name = target.rpartition('/')
    source = plan_entry['source']
    with _directory(root, parent, create=True) as parent_fd:
        temporary = '.studiohub-hf-' + uuid.uuid4().hex
        try:
            try:
                # A hard link is the whole point: the engine's cache entry and the verified
                # library file are one inode, so the mirror costs no bytes and cannot drift.
                os.link(root / source, temporary, dst_dir_fd=parent_fd, follow_symlinks=True)
                outcome = 'linked'
            except OSError:
                # A different volume (the cache root is a mount), a link limit, or a filesystem
                # without hard links: copy instead, and verify the copy against the manifest.
                outcome = 'copied'
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=parent_fd)
                with os.fdopen(descriptor, 'wb') as destination, \
                        _open_regular(root, source) as stream:
                    _check_stream(stream, entry, destination)
                    destination.flush()
                    os.fsync(destination.fileno())
            os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
    if not _target_matches(root, target, entry):
        raise PackageError(f'the cache entry written for {entry["path"]} does not match its manifest')
    return outcome


def _write_default_ref(root, repo, revision):
    """Point ``refs/<default>`` at the verified revision, and never replace an existing ref.

    Written with no trailing newline, because that is what huggingface_hub reads: its writer is
    ``_cache_commit_hash_for_specific_revision`` (``file_download.py:716-732`` in 1.31.0, the
    version the workers ship) which does ``write_text(commit_hash)``, and its reader does
    ``commit_hash = f.read()`` with no strip - so a newline here becomes part of the commit hash
    and the snapshot stops resolving. Verified against the installed version, not assumed.
    """
    target = '/'.join((HF_HUB_DIRNAME, _hf_repo_folder(repo), 'refs', HF_DEFAULT_REF))
    parent, _, name = target.rpartition('/')
    if os.path.lexists(root / target):
        return 'present'
    with _directory(root, parent, create=True) as parent_fd:
        temporary = '.studiohub-ref-' + uuid.uuid4().hex
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent_fd)
            with os.fdopen(descriptor, 'w') as stream:
                stream.write(revision)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Link, not rename: a ref that appeared while this one was being written - a real
                # download's own ref, say - must win. Rename would clobber it.
                os.link(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                outcome = 'written'
            except FileExistsError:
                outcome = 'present'
            os.fsync(parent_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
    return outcome


def materialize_companions(manifest, library, *, catalog=None):
    """Mirror every installed companion package into the Hugging Face cache the engines read.

    Idempotent: a file already present with the manifest's bytes is left alone, a second run
    rewrites nothing, and the hard link makes the mirror free while the library is on one volume
    (a copy is the fallback). The result is a report, never an exception for a library that
    cannot be written: an install whose companion could not be placed says so, by name, instead
    of reporting a success whose failure would appear at generation time.

    Returns ``{'state', 'reason', 'files', 'packages'}`` where ``state`` is ``ready``,
    ``degraded`` or ``not_required``.
    """
    root = _library(library)
    report = {'state': 'not_required', 'reason': None, 'files': {}, 'packages': {}}
    if not required_packages(manifest):
        return report
    plan, problems = _companion_plan(root, manifest, catalog)
    outcomes = {'linked': 0, 'copied': 0, 'present': 0}
    refs = set()
    for target in sorted(plan):
        item = plan[target]
        try:
            outcome = _write_cache_file(root, target, item)
        except (OSError, PackageError) as error:
            problems.append(
                f"could not place companion file {item['entry']['path']} of "
                f"{item['package_id']!r} into the engine cache at <library>/{target}: "
                f"{error.__class__.__name__}: {error}")
            continue
        outcomes[outcome] += 1
        report['files'][target] = outcome
        counted = report['packages'].setdefault(
            item['package_id'], {'files': 0, 'linked': 0, 'copied': 0, 'present': 0})
        counted['files'] += 1
        counted[outcome] += 1
        refs.add((item['repo'], item['revision']))
    for repo, revision in sorted(refs):
        try:
            _write_default_ref(root, repo, revision)
        except (OSError, PackageError) as error:
            problems.append(
                f'could not write the {HF_DEFAULT_REF!r} ref for {repo} at '
                f'<library>/{HF_HUB_DIRNAME}/{_hf_repo_folder(repo)}/refs/{HF_DEFAULT_REF}: '
                f'{error.__class__.__name__}: {error}')
    if problems:
        return {**report, 'state': 'degraded', 'reason': '; '.join(problems), 'written': outcomes}
    return {**report, 'state': 'ready', 'written': outcomes}


# One repair attempt per package per library per process, like `_repaired` above: a receipt
# copied from another Mac (or written before this build) records verified packages whose
# companions never reached the engine's cache. Reporting that forever would leave the offline
# failure one button press away; the first look at the package repairs it instead, and a library
# that cannot be written is reported by name rather than raised at a read-only caller.
#
# Keyed by library as well as package: the Hub can be pointed at a different model library while
# it runs, and a package repaired in one of them says nothing about the other.
_companion_repaired: set[tuple[str, str]] = set()


def _repair_companion_cache(manifest, root, catalog) -> bool:
    key = (str(root), manifest['package_id'])
    if key in _companion_repaired:
        return False
    _companion_repaired.add(key)
    try:
        report = materialize_companions(manifest, root, catalog=catalog)
    except (OSError, KeyError, TypeError, PackageError):
        return False
    return report['state'] == 'ready'


def verify_package(manifest, folder, *, library_root=None, allow_state=False):
    validate_manifest(manifest)
    folder = Path(folder).absolute()
    open_root = folder
    prefix = ''
    if library_root is not None:
        open_root = _library(library_root)
        try:
            prefix = folder.relative_to(open_root).as_posix()
        except (ValueError, PackageError):
            raise PackageError('Installed model folder is outside the selected library') from None
        if not allow_state and '.studiohub' in prefix.split('/'):
            raise PackageError('Installed model folder is inside private state')
        try:
            with _directory(open_root, prefix):
                pass
        except OSError:
            raise PackageError('Installed model folder is unavailable or contains a symlink') from None
    verified = {}
    for entry in manifest['files']:
        relative = _file_path(folder, entry)
        try:
            anchored = '/'.join(part for part in (prefix, relative) if part)
            with _open_regular(open_root, anchored) as stream:
                before = _stamp(os.fstat(stream.fileno()))
                _check_stream(stream, entry)
                after = _stamp(os.fstat(stream.fileno()))
                if before != after:
                    raise PackageError('File changed during verification')
                verified[entry['path']] = {'relative': relative, **after}
        except (OSError, PackageError) as error:
            raise PackageError(f'Invalid model file {entry["path"]}: {error}') from None
    return verified


def _write_index(root, index):
    """Replace ``.studiohub/installed.json`` atomically (0600, fsynced), anchored at the library."""
    with _directory(root, '.studiohub') as state_fd:
        name = 'index-' + uuid.uuid4().hex
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=state_fd)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(index, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, 'installed.json', src_dir_fd=state_fd, dst_dir_fd=state_fd)
            os.fsync(state_fd)
        finally:
            try:
                os.unlink(name, dir_fd=state_fd)
            except FileNotFoundError:
                pass


def forget_packages(library, package_ids):
    """Drop the receipts of packages whose folders are gone; return the package ids dropped.

    Used when the Hub moves a retired or shelved package into the Trash Folder: the receipt is then a
    line for bytes nobody will find (the verify-kit check and the kit manifest read the whole index and
    count every one as missing files). Takes the same import lock and writes the same format as
    `_register`. A receipt whose folder is still on disk is kept; a busy lock or an unreadable index
    changes nothing and raises `PackageError`, so the caller tries again at the next start.
    """
    root = _library(library)
    if not (root / '.studiohub/installed.json').is_file():
        return []
    with _lock(root):
        index = _index(root)
        gone = []
        for package_id in package_ids:
            record = index.get(package_id)
            if record is None:
                continue
            folders = [package_id]
            try:
                folders.append(_relative(record.get('folder') or ''))
            except PackageError:
                pass
            if not any(os.path.lexists(root / folder) for folder in folders):
                del index[package_id]
                gone.append(package_id)
        if gone:
            _write_index(root, index)
    return gone


def _register(manifest, folder, root):
    verified = verify_package(manifest, folder, library_root=root)
    index = _index(root)
    index[manifest['package_id']] = {
        'revision': manifest['revision'], 'manifest_sha256': manifest_digest(manifest),
        'folder': folder.relative_to(root).as_posix(), 'files': verified,
    }
    _write_index(root, index)
    return folder


def _repair_if_stale(manifest, record, root):
    """Rehash and re-record a receipt that was written in a different shape.

    Compares the field names, not their values: a package whose files are untouched but whose
    receipt lacks a field this build writes is not damaged, and rehashing once is cheaper than
    telling the operator their library is broken. Verified this session so a package is repaired
    at most once per process.
    """
    global _repaired
    package_id = manifest['package_id']
    if package_id in _repaired:
        return
    recorded = set()
    for saved in record.get('files', {}).values():
        recorded.update(k for k in saved if k != 'relative')
    if recorded == set(_STAMP_FIELDS):
        return
    with _lock(_library(root)):
        _register(manifest, root / _relative(record['folder']), _library(root))
    _repaired.add(package_id)


_repaired: set[str] = set()


def _not_installed_reason(manifest, folder_relative=None, relative=None):
    """The one sentence for "the bytes are not there", shared by both checks.

    `verify_installed` already publishes this for the fact; the cheap receipt path publishes the
    same words, so an operator reading a model row cannot get two descriptions of one absence
    depending on which check happened to notice it.
    """
    package_id = manifest['package_id']
    if relative is not None:
        return (
            f"{package_id} has no file at <library>/{folder_relative}/{relative}: its receipt "
            f"records the file, but it is not there. Install it in Models (or copy its folder "
            f"in) and verify again; its manifest is model-manifests/{package_id}.json.")
    return (
        f"{package_id} has no files in this library: neither "
        f"<library>/{package_id}/{manifest['revision']} nor the folder its receipt "
        f"records is present. Install it in Models (or copy its folder in) and verify "
        f"again; its manifest is model-manifests/{package_id}.json.")


def _library_bytes(manifest, record, root):
    """Where the receipt's bytes are, as ``(absent_reason, present, folder_relative)``.

    `absent_reason` is the `not_installed` sentence when the folder the receipt names, or one of
    the files this manifest lists inside it, is not on disk. `present` is True only when the
    folder and every listed file were found and the receipt's shape was readable, so a caller can
    say "present but does not match" truthfully. Existence is `lexists`: a dangling symlink is
    still something on disk, and the check that follows names it rather than calling the package
    absent.

    A receipt whose own shape this build cannot read answers ``(None, False, folder-or-None)``.
    That is a mismatch to report, never evidence that the package is not installed.
    """
    try:
        folder_relative = _relative(record.get('folder') or '')
    except (AttributeError, PackageError):
        return None, False, None
    if not os.path.lexists(root / folder_relative):
        return _not_installed_reason(manifest), False, folder_relative
    files = record.get('files')
    if not isinstance(files, dict):
        return None, False, folder_relative
    for entry in manifest['files']:
        saved = files.get(entry['path'])
        if not isinstance(saved, dict):
            return None, False, folder_relative
        try:
            relative = _relative(saved.get('relative') or '')
        except PackageError:
            return None, False, folder_relative
        if relative not in (entry['path'], entry.get('source_path')):
            return None, False, folder_relative
        if not os.path.lexists(root / folder_relative / relative):
            return (_not_installed_reason(manifest, folder_relative, relative), False,
                    folder_relative)
    return None, True, folder_relative


_ERRNO_PREFIX = re.compile(r'\[Errno \d+\]\s*')


def _oserror_sentence(error):
    """What an `OSError` says, in words.

    `str(OSError)` is `[Errno 2] No such file or directory: 'x'` - an error number and a relative
    name, which is not something a person can act on.
    """
    what = (getattr(error, 'strerror', None) or error.__class__.__name__).strip().rstrip('.')
    subject = getattr(error, 'filename', None)
    return f'{what} while reading {subject!r}' if subject else what


def _receipt_failure(manifest, record, root, error):
    """The row `preflight` publishes when the cached receipt does not check out.

    Two facts were conflated here. A package whose bytes are not on disk is `not_installed`, the
    state `verify_installed` already gives it; `preflight` answered `invalid`, so six packages on
    the owner's Mac published `[Errno 2] No such file or directory: 'voxcpm2-4bit'` under their
    names - "absent" and "corrupt" made the same state, and the reason was a Python exception.
    Bytes that are there and do not match stay `invalid`, named by file and by the manifest they
    must match. Nothing here makes a mismatch pass: absence is a different state, not a softer
    one.
    """
    package_id = manifest['package_id']
    absent, present, folder_relative = _library_bytes(manifest, record, root)
    if absent is not None:
        return {'state': 'not_installed', 'reason': absent}
    if not present and isinstance(error, (FileNotFoundError, NotADirectoryError)):
        # The scan above could not confirm the receipt's paths and the read says one is not
        # there - absent bytes rather than damage, and still not an errno. When the scan *did*
        # find every path, a NotADirectoryError is the open refusing a symlink where a directory
        # is required: bytes that are present and not this package's layout, which stays
        # `invalid` and is named below.
        return {'state': 'not_installed',
                'reason': _not_installed_reason(manifest, folder_relative)}
    manifest_name = f'model-manifests/{package_id}.json'
    if isinstance(error, PackageError):
        # Every PackageError on this path is written for the operator ("<file> changed since it
        # was verified: ...", "Unexpected installed file mapping"). The one exception is
        # `verify_package`'s wrapper, which embeds the errno text of the OSError it caught, so
        # an errno in it is dropped rather than republished.
        detail = _ERRNO_PREFIX.sub('', str(error)).strip()
    elif isinstance(error, OSError):
        detail = _oserror_sentence(error)
    elif isinstance(error, KeyError):
        missing = error.args[0] if error.args else 'a field'
        detail = f'{missing!r} is missing from <library>/.studiohub/installed.json'
    else:
        detail = (f'<library>/.studiohub/installed.json is not in a shape this build can read '
                  f'({error.__class__.__name__})')
    if present:
        return {'state': 'invalid', 'reason': (
            f'{package_id} is present at <library>/{folder_relative} but does not match '
            f'{manifest_name}: {detail}. Replace the named file (or reinstall the package) and '
            f'verify again.')}
    return {'state': 'invalid', 'reason': (
        f"{package_id}'s receipt could not be checked against {manifest_name}: {detail}. Run "
        f'Verify Files to rehash the package and rewrite its receipt.')}


def load_catalog(directory) -> dict:
    """Every manifest in a directory, keyed by package id.

    A package's **companions are named by other manifests**, so a check that cannot see them
    reports `dependency_missing` for a package whose companion is installed, verified and sitting
    beside it. The Hub builds its own catalog map for its routes (it must degrade per manifest
    rather than raise); this is the same walk for the workers, which read the `model-manifests`
    directory out of their own bundle.

    A manifest that cannot be parsed is skipped rather than raising: one bad file must not stop a
    worker from reading the rest, and the package it describes reports its own state.
    """
    from functools import lru_cache

    @lru_cache(maxsize=4)
    def _read(root: str) -> dict:
        catalog: dict = {}
        for path in sorted(Path(root).glob('*.json')):
            try:
                manifest = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            package_id = manifest.get('package_id') if isinstance(manifest, dict) else None
            if isinstance(package_id, str) and package_id:
                catalog[package_id] = manifest
        return catalog

    return _read(str(Path(directory).resolve()))


def preflight(manifest, library, *, catalog=None, _seen=None):
    """Cheap readability/change check against a previous full verification receipt.

    files_verified is deliberately not ready-to-generate: the Hub must still check
    runtime identity, OS/RAM eligibility, paused models and device admission.
    """
    digest = manifest_digest(manifest)
    root = _library(library)
    record = _index(root).get(manifest['package_id'])
    base = {'package_id': manifest['package_id'], 'manifest_sha256': digest}
    if not record or record.get('manifest_sha256') != digest:
        # Two different situations, and they need different instructions.
        #
        # No receipt at all: nothing was ever installed here, and saying so is what the operator
        # needs. A receipt whose digest no longer matches the manifest: *something* is installed,
        # under an older manifest, and this cheap path cannot say whether the bytes are still
        # right — only a full verify can, and it rewrites the receipt when they are. Reporting
        # both as a bare `not_installed` with no reason left the app to supply its own sentence,
        # and its sentence is "download it" — wrong, and wrong in the direction that makes an
        # operator re-download a model he already has.
        if not record:
            return {**base, 'state': 'not_installed',
                    'reason': _not_installed_reason(manifest)}
        return {**base, 'state': 'not_installed', 'reason': (
            f"{manifest['package_id']} was installed under a different manifest, so its files "
            f"cannot be confirmed against the current one by the cheap check. Open Models and "
            f"press Verify Files: if the files are right it will record them again, and if they "
            f"are not it will name the file that differs. Its manifest is "
            f"model-manifests/{manifest['package_id']}.json.")}
    # A receipt written by a different build may record different fields. That is a stale cache,
    # not a damaged package, so rehash once and repair it. Without this, changing what the receipt
    # records turned every installed model into `invalid` on every machine - which happened, twice,
    # to the owner. A cache that fails instead of repairing itself is worse than no cache.
    try:
        _repair_if_stale(manifest, record, root)
    except (OSError, KeyError, TypeError, PackageError) as error:
        return {**base, **_receipt_failure(manifest, record, root, error)}
    record = _index(root).get(manifest['package_id']) or record
    try:
        folder_relative = _relative(record['folder'])
        folder = root / folder_relative
        # Anchor every lookup at the library, not a possibly replaced package parent.
        for entry in manifest['files']:
            saved = record['files'][entry['path']]
            relative = _relative(saved['relative'])
            if relative not in (entry['path'], entry.get('source_path')):
                raise PackageError('Unexpected installed file mapping')
            with _open_regular(root, folder_relative + '/' + relative) as stream:
                measured = _stamp(os.fstat(stream.fileno()))
                recorded = {k: saved[k] for k in measured}
                if measured != recorded:
                    # Which file, and what it measures against the receipt. "File changed" alone
                    # named the file but not the expectation, and this reason is published on the
                    # Hub row the app renders.
                    raise PackageError(
                        f'{entry["path"]} changed since it was verified: '
                        f'{measured["st_size"]} bytes, mtime {measured["mtime_s"]}, where its '
                        f'receipt records {recorded["st_size"]} bytes, mtime '
                        f'{recorded["mtime_s"]}')
        seen = set(_seen or ())
        if manifest['package_id'] in seen:
            raise PackageError('Companion dependency cycle')
        seen.add(manifest['package_id'])
        for dependency in required_packages(manifest):
            companion = (catalog or {}).get(dependency)
            if (not companion or companion.get('package_id') != dependency
                    or preflight(companion, root, catalog=catalog, _seen=seen)['state'] != 'files_verified'):
                return {**base, 'state': 'dependency_missing', 'dependency': dependency}
        # The companion's bytes must also be where its engine's own lookup finds them, not only
        # in the library. A receipt copied from another Mac - or written before this build - has
        # verified packages whose companions never reached the cache; repair that once here (the
        # same self-healing `_repair_if_stale` above performs for a receipt in an older shape),
        # and when it still is not true say so by name instead of letting generation discover it.
        cache = companion_cache_status(manifest, root, catalog=catalog)
        if cache['state'] != 'ready' and _repair_companion_cache(manifest, root, catalog):
            cache = companion_cache_status(manifest, root, catalog=catalog)
        if cache['state'] not in ('ready', 'not_required'):
            return {**base, 'state': COMPANION_CACHE_STATE, 'reason': cache['reason'],
                    'companion_cache': cache}
    except (OSError, KeyError, TypeError, PackageError) as error:
        return {**base, **_receipt_failure(manifest, record, root, error)}
    return {**base, 'state': 'files_verified', 'folder': str(folder),
            'engine': manifest['engine'], 'minimum_macos': manifest['minimum_macos'],
            'verification': 'cached_receipt', 'companion_cache': cache}


def shared_files(manifest, library, *, catalog=None):
    """The files another Mac may copy of this package: ``{manifest path: absolute path}``.

    Only for a package this Mac has installed and verified (the receipt check `preflight` makes),
    and only the manifest's own files at the paths the receipt recorded - nothing else in the
    library is reachable through a share. Raises `PackageError` with the reason otherwise.
    """
    report = preflight(manifest, library, catalog=catalog)
    if report['state'] != 'files_verified':
        raise PackageError(report.get('reason') or f"{manifest['package_id']} is {report['state']} on this Mac")
    root = _library(library)
    record = _index(root)[manifest['package_id']]
    folder = root / _relative(record['folder'])
    return {entry['path']: folder / _relative(record['files'][entry['path']]['relative'])
            for entry in manifest['files']}


def _installed_folder(root, manifest, record):
    """The library-relative folder this package's bytes are in, or None.

    Two pieces of evidence, and the order between them is the whole decision:

    * the folder the **receipt** names - the only record of an install performed under a folder
      name that is not the conventional one, because `import_folder` keeps the name of a folder
      that is already inside the library (`test_in_library_registration_never_makes_second_copy`);
    * the conventional ``<package_id>/<revision>`` that `_publish` and the kits write, and where
      the owner's chat model's 5.6 GB sits with **no receipt at all**.

    A receipt that names a *different* revision describes other bytes, so the conventional folder
    is read first in that one case; the older folder is still tried when there is nothing at the
    conventional path, because its bytes may be exactly what the current manifest describes.

    Existence, not readability, is all this decides. A folder that is present with damaged or
    wrong bytes must reach the verifier and be reported by name - returning `not_installed`
    because the *shape* of the evidence was unexpected is the dead end this function exists to
    remove.
    """
    receipt = None
    if isinstance(record, dict):
        try:
            receipt = _relative(record.get('folder') or '')
        except PackageError:
            receipt = None
    conventional = _relative(f"{manifest['package_id']}/{manifest['revision']}")
    # `revision` has been written by `_register` since the first build; a hand-written receipt
    # that omits it cannot be said to describe another revision, so it is treated as this one's.
    same_revision = record.get('revision') in (None, manifest['revision']) if receipt else False
    for relative in ([receipt, conventional] if same_revision else [conventional, receipt]):
        if relative and os.path.lexists(root / relative):
            return relative
    return None


def verify_installed(manifest, library, *, catalog=None):
    """Rehash the installed package, record it, and place its companions.

    This is the explicit operator path - the button labelled Verify Files - so it verifies the
    files that are **there**, not only the ones a previous verify already recorded:

    * no receipt, bytes at the conventional ``<library>/<package_id>/<revision>`` (how the kits,
      the copy script and a manual install leave a package): hash them and write the receipt;
    * a receipt whose digest the current manifest no longer matches - `chatterbox-{4,8}bit` after
      their companion field moved to `requires_packages`, which changed their digest under a
      receipt nothing rewrote: verify the current manifest against the files and rewrite it;
    * nothing on disk: `not_installed`, with the paths that were looked for, so "install it" is
      the instruction the state actually supports.

    It never downloads and never writes a model file. Bytes that do not match are refused by
    name - package, file, measured reason, and the manifest to fix - and no receipt is written,
    so a failed verify cannot turn into a silent pass.
    """
    validate_manifest(manifest)
    root = _library(library)
    package_id = manifest['package_id']
    digest = manifest_digest(manifest)
    base = {'package_id': package_id, 'manifest_sha256': digest}
    try:
        with _lock(root):
            record = _index(root).get(package_id)
            folder_relative = _installed_folder(root, manifest, record)
            if folder_relative is None:
                return {**base, 'state': 'not_installed',
                        'reason': _not_installed_reason(manifest)}
            folder = root / folder_relative
            try:
                # Full verification also refreshes the receipt so a valid touched file
                # does not remain permanently stale in the cheap preflight path.
                _register(manifest, folder, root)
            except PackageError as error:
                # The bytes are there and they are not this manifest's. Say what was measured,
                # where, and which file to fix - and record nothing, so the next reader of
                # `installed.json` cannot mistake this for a pass.
                return {**base, 'state': 'invalid', 'reason': (
                    f"{package_id} is present at <library>/{folder_relative} but does not match "
                    f"model-manifests/{package_id}.json: {error}. Replace the named file (or "
                    f"reinstall the package); nothing was recorded and no model file was "
                    f"changed."), 'verification': 'full_rehash'}
        result = {**base, 'state': 'files_verified', 'folder': str(folder),
                  'engine': manifest['engine'], 'minimum_macos': manifest['minimum_macos'],
                  'verification': 'full_rehash'}
        seen = {manifest['package_id']}
        for dependency in required_packages(manifest):
            companion = (catalog or {}).get(dependency)
            if not companion or companion.get('package_id') != dependency:
                return {**result, 'state': 'dependency_missing', 'dependency': dependency}
            if preflight(companion, root, catalog=catalog, _seen=seen)['state'] != 'files_verified':
                return {**result, 'state': 'dependency_missing', 'dependency': dependency}
        # An explicit verify is where the operator asks for the whole thing to be true, so this
        # is where a companion's bytes are placed in the cache the engine reads. A failure is
        # reported with its reason rather than swallowed: the package's own files are verified,
        # but the offline generation the companion exists for would still fail.
        try:
            cache = materialize_companions(manifest, root, catalog=catalog)
        except (OSError, PackageError) as error:
            # The package's own bytes are verified; only the engine's access to its companion
            # failed, so this is a named degradation rather than an invalid package.
            cache = {'state': 'degraded', 'reason': f'{error.__class__.__name__}: {error}',
                     'files': {}}
        if cache['state'] == 'degraded':
            return {**result, 'state': COMPANION_CACHE_STATE, 'reason': cache['reason'],
                    'companion_cache': cache}
        return {**result, 'companion_cache': cache}
    except (OSError, KeyError, TypeError, PackageError) as error:
        # Infrastructure around the hash - an unreadable receipt, a library that cannot be
        # locked, a catalog that does not answer. Named with the package and its manifest so the
        # message points at something to look at rather than a bare exception.
        return {**base, 'state': 'invalid',
                'reason': (f'{package_id} could not be verified: {error}. Its manifest is '
                           f'model-manifests/{package_id}.json.'),
                'verification': 'full_rehash'}


def _stage(manifest, root):
    return _mkdir(root, '.studiohub/staging/' + manifest['package_id'] + '-' + manifest_digest(manifest))


def _publish(manifest, staged, root):
    verify_package(manifest, staged, library_root=root, allow_state=True)
    parent = _mkdir(root, manifest['package_id'])
    destination = parent / manifest['revision']
    if destination.exists() or destination.is_symlink():
        try:
            verify_package(manifest, destination, library_root=root)
        except (OSError, PackageError):
            # Preserve a corrupt/older folder for explicit owner disposition; never overwrite readers.
            destination = parent / (manifest['revision'] + '-' + uuid.uuid4().hex[:12])
        else:
            return _register(manifest, destination, root)
    with _directory(root, '.studiohub/staging') as source_fd, \
            _directory(root, manifest['package_id']) as destination_fd:
        os.rename(staged.name, destination.name, src_dir_fd=source_fd, dst_dir_fd=destination_fd)
        os.fsync(destination_fd)
        os.fsync(source_fd)
    return _register(manifest, destination, root)


def import_folder(manifest, source, library, *, catalog=None):
    """Install one package folder, then place its companions where their engines read them.

    ``catalog`` is the manifest set the companion relationships are resolved against. An import
    can be performed without it (the caller may only have this manifest), in which case a package
    with companions records a verified install whose companion cache is not yet written; the next
    catalog-bearing verify or scan writes it, and until then the package reports
    :data:`COMPANION_CACHE_STATE` naming the companion rather than claiming to be ready.
    """
    validate_manifest(manifest)
    root = _library(library)
    source = Path(source).expanduser().absolute()
    if source.is_symlink() or not source.is_dir():
        raise PackageError('Select an existing model folder, not a folder symlink')
    source = source.resolve(strict=True)
    # Validate source before creating state, so a failed import leaves the library untouched.
    for entry in manifest['files']:
        try:
            with _source_stream(source, entry) as stream:
                _check_stream(stream, entry)
        except OSError:
            raise PackageError(f'Missing or unreadable model file: {entry["path"]}') from None
    with _lock(root):
        if source.is_relative_to(root):
            _relative(source.relative_to(root).as_posix())
            installed = _register(manifest, source, root)
        else:
            staged = _stage(manifest, root)
            for entry in manifest['files']:
                target = staged / entry['path']
                relative = target.parent.relative_to(root).as_posix()
                with _directory(root, relative, create=True) as parent_fd:
                    temporary = '.copy-' + uuid.uuid4().hex
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent_fd)
                    try:
                        with os.fdopen(fd, 'wb') as out, _source_stream(source, entry) as stream:
                            _check_stream(stream, entry, out)
                            out.flush()
                            os.fsync(out.fileno())
                        os.replace(temporary, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                        os.fsync(parent_fd)
                    finally:
                        try:
                            os.unlink(temporary, dir_fd=parent_fd)
                        except FileNotFoundError:
                            pass
            installed = _publish(manifest, staged, root)
    materialize_companions(manifest, root, catalog=catalog)
    return installed


class _SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _open_download(request, **kwargs):
    return urllib.request.build_opener(_SecureRedirect()).open(request, **kwargs)


def _download_file(entry, staged, urls, root, peer=None):
    """Fetch one file into staging; returns where its bytes came from (``lan``/``internet``), or
    None when staging already held it. ``peer`` (``{'url_base': ...}``) is tried before ``urls``: bytes
    that fail verification fall through to the internet for this file only, and a peer that does not
    answer (unreachable, 404 after expiry, 429 busy) is dropped for the rest of the run."""
    target = staged / entry['path']
    relative = target.parent.relative_to(root).as_posix()
    with _directory(root, relative, create=True) as parent_fd:
        try:
            with os.fdopen(os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                   dir_fd=parent_fd), 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise PackageError('Regular model file required')
                _check_stream(stream, entry)
            return
        except (OSError, PackageError):
            pass
        partial = target.name + '.part'
        lan_url = (peer['url_base'] + '/' + urllib.parse.quote(entry['path'])
                   if peer and peer.get('url_base') else None)
        for url in ([lan_url] if lan_url else []) + list(urls):
            lan = url is lan_url
            if not lan:
                _url(url)
            try:
                info = os.stat(partial, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise PackageError(f'Unsafe partial model file: {entry["path"]}')
                offset = info.st_size
            except FileNotFoundError:
                offset = 0
            if offset >= entry['size']:
                try:
                    os.unlink(partial, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
                offset = 0
            request = urllib.request.Request(url, headers={'Accept-Encoding': 'identity'})
            if offset:
                request.add_header('Range', f'bytes={offset}-')
            try:
                with _open_download(request, timeout=30) as response:
                    if response.status == 206:
                        expected = f'bytes {offset}-{entry["size"] - 1}/{entry["size"]}'
                        if response.headers.get('Content-Range') != expected:
                            raise PackageError('Invalid partial-content response')
                    elif response.status == 200:
                        offset = 0
                    else:
                        raise PackageError('Unexpected download response')
                    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
                    flags |= os.O_APPEND if offset else os.O_TRUNC
                    with os.fdopen(os.open(partial, flags, 0o600, dir_fd=parent_fd), 'wb') as out:
                        if not stat.S_ISREG(os.fstat(out.fileno()).st_mode):
                            raise PackageError('Regular partial file required')
                        size = offset
                        for chunk in iter(lambda: response.read(1024 * 1024), b''):
                            size += len(chunk)
                            if size > entry['size']:
                                raise PackageError('Download exceeds expected file size')
                            out.write(chunk)
                        out.flush()
                        os.fsync(out.fileno())
                with os.fdopen(os.open(partial, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                       dir_fd=parent_fd), 'rb') as stream:
                    _check_stream(stream, entry)
                os.replace(partial, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                os.fsync(parent_fd)
                if lan:
                    peer['copied'] = True
                return 'lan' if lan else 'internet'
            except PackageError:
                try:
                    os.unlink(partial, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
                # A later explicit retry resumes this file; completed files remain verified.
                if lan:
                    # The run reads this line while it runs (model_downloads._lan_fallback): why the copy
                    # stopped, in one line, never the token-bearing share URL.
                    reason = ' '.join(str(error).replace(peer['url_base'], '<share>').split())
                    print(f"LAN copy failed, using the internet ({'mixed' if peer.get('copied') else 'internet'}): "
                          f"{type(error).__name__}: {reason}", flush=True)
                    peer['url_base'] = None
                continue
    raise PackageError(f'Download incomplete or invalid: {entry["path"]}')


def _download_one(manifest, root, sources, peer=None, used=None):
    validate_manifest(manifest)
    with _lock(root):
        current = preflight(manifest, root)
        if current['state'] in ('files_verified', 'dependency_missing'):
            record = _index(root)[manifest['package_id']]
            return root / record['folder']
        staged = _stage(manifest, root)
        for entry in manifest['files']:
            urls = (sources or {}).get(entry['path'], entry.get('urls', []))
            if not isinstance(urls, list) or not urls:
                raise PackageError(f'No download source for {entry["path"]}')
            got = _download_file(entry, staged, urls, root, peer)
            if got and used is not None:
                used.add(got)
        return _publish(manifest, staged, root)


def download_package(manifest, library, *, sources=None, catalog=None, peer=None, used=None):
    """An explicit download includes its catalog-declared companion packages.

    ``peer`` is another Mac's share of this package (`peer_base`), tried before the internet for
    this package's own files (companions always come from their own sources); ``used`` collects
    ``lan``/``internet`` for every file fetched."""
    root = _library(library)
    peer = {'url_base': peer_base(peer)} if peer else None
    ordered = []
    visited = set()

    def collect(current, active):
        validate_manifest(current)
        identity = current['package_id']
        if identity in active:
            raise PackageError('Companion dependency cycle')
        if identity in visited:
            return
        for dependency in required_packages(current):
            companion = (catalog or {}).get(dependency)
            if not companion or companion.get('package_id') != dependency:
                raise PackageError(f'Trusted companion manifest required: {dependency}')
            collect(companion, active | {identity})
        visited.add(identity)
        ordered.append(current)

    collect(manifest, set())
    for current in ordered:
        mine = current is manifest
        result = _download_one(current, root, sources if mine else None, peer if mine else None, used)
    # Every package this download installed, companions included, is mirrored into the engine
    # cache: a downloaded kit is exactly the case where the network is about to go away.
    for current in ordered:
        materialize_companions(current, root, catalog=catalog)
    return result


def scan_library(manifests, library, *, catalog=None):
    manifests = list(manifests)
    catalog = {**(catalog or {}), **{m['package_id']: m for m in manifests}}
    root = _library(library)
    candidates = [p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith('.')]
    results = []
    for manifest in manifests:
        validate_manifest(manifest)
        report = preflight(manifest, root, catalog=catalog)
        if report['state'] in ('files_verified', 'dependency_missing'):
            results.append(report)
            continue
        folders = list(candidates)
        canonical = root / manifest['package_id']
        if canonical in candidates:
            folders.extend(p for p in canonical.iterdir() if p.is_dir() and not p.is_symlink())
        for folder in folders:
            try:
                verify_package(manifest, folder)
            except (OSError, PackageError):
                continue
            import_folder(manifest, folder, root, catalog=catalog)
            report = preflight(manifest, root, catalog=catalog)
            break
        results.append(report)
    # A library copied onto this Mac carries receipts, not the engine cache those receipts were
    # written against: place every companion before reporting, so a scan is what makes a kit node
    # able to generate with the network off.
    for manifest in manifests:
        materialize_companions(manifest, root, catalog=catalog)
    # A later scanned package may satisfy an earlier package's dependency.
    return [preflight(manifest, root, catalog=catalog) for manifest in manifests]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('import', 'download', 'verify', 'scan'))
    parser.add_argument('--library', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path, help='Trusted catalog JSON; scan accepts a directory')
    parser.add_argument('--source', type=Path, help='Source folder for import')
    parser.add_argument('--sources', type=Path, help='Optional JSON mapping of file paths to HTTPS mirror URL lists')
    parser.add_argument('--peer', type=Path, help="Optional JSON string: another Mac's share URL, tried first")
    args = parser.parse_args()
    try:
        if args.action == 'scan':
            manifests = [json.loads(p.read_text()) for p in sorted(args.manifest.glob('*.json'))]
            result = scan_library(manifests, args.library)
        else:
            manifest = json.loads(args.manifest.read_text())
            catalog = {}
            for path in args.manifest.parent.glob('*.json'):
                candidate = json.loads(path.read_text())
                validate_manifest(candidate)
                catalog[candidate['package_id']] = candidate
            if args.action == 'import':
                if args.source is None:
                    raise PackageError('--source is required for import')
                folder = import_folder(manifest, args.source, args.library, catalog=catalog)
                result = {'folder': str(folder),
                          'companion_cache': companion_cache_status(manifest, args.library,
                                                                    catalog=catalog)}
            elif args.action == 'download':
                sources = json.loads(args.sources.read_text()) if args.sources else None
                peer = json.loads(args.peer.read_text()) if args.peer else None
                used = set()
                folder = download_package(manifest, args.library, sources=sources, catalog=catalog,
                                          peer=peer, used=used)
                result = {'folder': str(folder),
                          'companion_cache': companion_cache_status(manifest, args.library,
                                                                    catalog=catalog),
                          'source_used': 'mixed' if len(used) > 1 else next(iter(used), None)}
            else:
                result = verify_installed(manifest, args.library, catalog=catalog)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
