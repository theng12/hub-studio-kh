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


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 1:
        raise PackageError('Unsupported model manifest schema')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,99}', str(manifest.get('package_id', ''))):
        raise PackageError('Invalid package_id')
    if not re.fullmatch(r'[0-9a-f]{40}', str(manifest.get('revision', ''))):
        raise PackageError('A full immutable model revision is required')
    if manifest.get('engine') not in ('image-mflux', 'voice-mlx', 'subtitle-mlx'):
        raise PackageError('Unsupported model engine')
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
    return manifest


def manifest_digest(manifest):
    validate_manifest(manifest)
    identity = {k: manifest[k] for k in ('schema_version', 'package_id', 'model_id',
                                        'revision', 'engine', 'minimum_macos')}
    identity['requires_packages'] = sorted(manifest.get('requires_packages', []))
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
    if size != entry['size'] or digest.hexdigest() != entry['sha256']:
        raise PackageError(f'Incomplete or invalid model file: {entry["path"]}')


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


def _register(manifest, folder, root):
    verified = verify_package(manifest, folder, library_root=root)
    index = _index(root)
    index[manifest['package_id']] = {
        'revision': manifest['revision'], 'manifest_sha256': manifest_digest(manifest),
        'folder': folder.relative_to(root).as_posix(), 'files': verified,
    }
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
        return {**base, 'state': 'not_installed'}
    # A receipt written by a different build may record different fields. That is a stale cache,
    # not a damaged package, so rehash once and repair it. Without this, changing what the receipt
    # records turned every installed model into `invalid` on every machine - which happened, twice,
    # to the owner. A cache that fails instead of repairing itself is worse than no cache.
    try:
        _repair_if_stale(manifest, record, root)
    except (OSError, KeyError, TypeError, PackageError) as error:
        return {**base, 'state': 'invalid', 'reason': str(error)}
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
                if _stamp(os.fstat(stream.fileno())) != {k: saved[k] for k in _stamp(os.fstat(stream.fileno()))}:
                    raise PackageError(f'File changed; verify again: {entry["path"]}')
        seen = set(_seen or ())
        if manifest['package_id'] in seen:
            raise PackageError('Companion dependency cycle')
        seen.add(manifest['package_id'])
        for dependency in manifest.get('requires_packages', []):
            companion = (catalog or {}).get(dependency)
            if (not companion or companion.get('package_id') != dependency
                    or preflight(companion, root, catalog=catalog, _seen=seen)['state'] != 'files_verified'):
                return {**base, 'state': 'dependency_missing', 'dependency': dependency}
    except (OSError, KeyError, TypeError, PackageError) as error:
        return {**base, 'state': 'invalid', 'reason': str(error)}
    return {**base, 'state': 'files_verified', 'folder': str(folder),
            'engine': manifest['engine'], 'minimum_macos': manifest['minimum_macos'],
            'verification': 'cached_receipt'}


def verify_installed(manifest, library, *, catalog=None):
    """Rehash the indexed package; this is the explicit operator verify path."""
    validate_manifest(manifest)
    root = _library(library)
    digest = manifest_digest(manifest)
    base = {'package_id': manifest['package_id'], 'manifest_sha256': digest}
    try:
        with _lock(root):
            record = _index(root).get(manifest['package_id'])
            if not record or record.get('manifest_sha256') != digest:
                return {**base, 'state': 'not_installed'}
            folder_relative = _relative(record['folder'])
            folder = root / folder_relative
            # Full verification also refreshes the receipt so a valid touched file
            # does not remain permanently stale in the cheap preflight path.
            _register(manifest, folder, root)
        result = {**base, 'state': 'files_verified', 'folder': str(folder),
                  'engine': manifest['engine'], 'minimum_macos': manifest['minimum_macos'],
                  'verification': 'full_rehash'}
        seen = {manifest['package_id']}
        for dependency in manifest.get('requires_packages', []):
            companion = (catalog or {}).get(dependency)
            if not companion or companion.get('package_id') != dependency:
                return {**result, 'state': 'dependency_missing', 'dependency': dependency}
            if preflight(companion, root, catalog=catalog, _seen=seen)['state'] != 'files_verified':
                return {**result, 'state': 'dependency_missing', 'dependency': dependency}
        return result
    except (OSError, KeyError, TypeError, PackageError) as error:
        return {**base, 'state': 'invalid', 'reason': str(error),
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


def import_folder(manifest, source, library):
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
            return _register(manifest, source, root)
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
        return _publish(manifest, staged, root)


class _SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _open_download(request, **kwargs):
    return urllib.request.build_opener(_SecureRedirect()).open(request, **kwargs)


def _download_file(entry, staged, urls, root):
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
        for url in urls:
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
                return
            except PackageError:
                try:
                    os.unlink(partial, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            except (OSError, urllib.error.URLError, http.client.HTTPException):
                # A later explicit retry resumes this file; completed files remain verified.
                continue
    raise PackageError(f'Download incomplete or invalid: {entry["path"]}')


def _download_one(manifest, root, sources):
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
            _download_file(entry, staged, urls, root)
        return _publish(manifest, staged, root)


def download_package(manifest, library, *, sources=None, catalog=None):
    """An explicit download includes its catalog-declared companion packages."""
    root = _library(library)
    ordered = []
    visited = set()

    def collect(current, active):
        validate_manifest(current)
        identity = current['package_id']
        if identity in active:
            raise PackageError('Companion dependency cycle')
        if identity in visited:
            return
        for dependency in current.get('requires_packages', []):
            companion = (catalog or {}).get(dependency)
            if not companion or companion.get('package_id') != dependency:
                raise PackageError(f'Trusted companion manifest required: {dependency}')
            collect(companion, active | {identity})
        visited.add(identity)
        ordered.append(current)

    collect(manifest, set())
    for current in ordered:
        result = _download_one(current, root, sources if current is manifest else None)
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
            import_folder(manifest, folder, root)
            report = preflight(manifest, root, catalog=catalog)
            break
        results.append(report)
    # A later scanned package may satisfy an earlier package's dependency.
    return [preflight(manifest, root, catalog=catalog) for manifest in manifests]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('import', 'download', 'verify', 'scan'))
    parser.add_argument('--library', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path, help='Trusted catalog JSON; scan accepts a directory')
    parser.add_argument('--source', type=Path, help='Source folder for import')
    parser.add_argument('--sources', type=Path, help='Optional JSON mapping of file paths to HTTPS mirror URL lists')
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
                result = {'folder': str(import_folder(manifest, args.source, args.library))}
            elif args.action == 'download':
                sources = json.loads(args.sources.read_text()) if args.sources else None
                result = {'folder': str(download_package(manifest, args.library, sources=sources, catalog=catalog))}
            else:
                result = verify_installed(manifest, args.library, catalog=catalog)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
