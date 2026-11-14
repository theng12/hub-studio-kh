"""Strict access to the operator's ``<data root>/render-authority/`` records.

Ported from Gateway ``private_render_auth._read_record``: every ancestor is walked by
descriptor without following links, the authority directories must be owner-only
(0700), a record must be one owner-only (0600) regular file with a single link and a
bounded size, and both the file and every directory are re-checked after the read so
a swap during the read is refused.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path
import secrets
import stat

AUTHORITY_DIRECTORY = "render-authority"
MAX_RECORD_BYTES = 16 * 1024
# Tests substitute a private fixture root when their volume ancestry is unsafe.
_ROOT_ANCHOR = Path("/")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class RenderAuthorityRecordError(ValueError):
    """A fixed, non-sensitive refusal code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str):
    raise RenderAuthorityRecordError(code) from None


def _plain_name(name: str) -> str:
    if not name or name in {".", ".."} or "/" in name or "\0" in name:
        _fail("UNSAFE_PATH")
    return name


def _directory_identity(value):
    return (value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode), value.st_uid,
            stat.S_IMODE(value.st_mode))


def _file_identity(value):
    return (value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode), value.st_uid,
            stat.S_IMODE(value.st_mode), value.st_nlink, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _validate_directory(value, *, expected_uid: int, policy: str) -> None:
    if not stat.S_ISDIR(value.st_mode):
        _fail("UNSAFE_PATH")
    mode = stat.S_IMODE(value.st_mode)
    if policy == "private":
        if value.st_uid != expected_uid or mode != 0o700:
            _fail("UNSAFE_PATH")
    elif policy == "data":
        if value.st_uid != expected_uid or mode & (stat.S_IWGRP | stat.S_IWOTH):
            _fail("UNSAFE_PATH")
    elif value.st_uid not in (0, expected_uid) or mode & (stat.S_IWGRP | stat.S_IWOTH):
        _fail("UNSAFE_PATH")


def _validate_file(value, *, expected_uid: int, max_bytes: int) -> None:
    if (not stat.S_ISREG(value.st_mode) or value.st_uid != expected_uid
            or stat.S_IMODE(value.st_mode) != 0o600 or value.st_nlink != 1
            or not 1 <= value.st_size <= max_bytes):
        _fail("UNSAFE_FILE")


def read_record(data_root: Path, *names: str, expected_uid: int,
                max_bytes: int = MAX_RECORD_BYTES) -> bytes:
    """Read ``data_root/render-authority/<names...>``; every refusal raises."""
    data_root = Path(data_root)
    if not data_root.is_absolute() or not names:
        _fail("UNSAFE_PATH")
    try:
        components = tuple(map(_plain_name, data_root.relative_to(_ROOT_ANCHOR).parts))
    except ValueError:
        _fail("UNSAFE_PATH")
    private = (AUTHORITY_DIRECTORY, *map(_plain_name, names[:-1]))
    filename = _plain_name(names[-1])
    descriptors: list[int] = []
    directories: list[tuple[int, str, int, str]] = []
    file_stage = False
    try:
        root_fd = os.open(_ROOT_ANCHOR, _DIR_FLAGS)
        descriptors.append(root_fd)
        root_policy = "data" if not components else "ancestor"
        _validate_directory(os.fstat(root_fd), expected_uid=expected_uid, policy=root_policy)
        parent_fd = root_fd
        walk = [(name, "data" if index == len(components) - 1 else "ancestor")
                for index, name in enumerate(components)]
        walk += [(name, "private") for name in private]
        for name, policy in walk:
            child_fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
            descriptors.append(child_fd)
            _validate_directory(os.fstat(child_fd), expected_uid=expected_uid, policy=policy)
            directories.append((parent_fd, name, child_fd, policy))
            parent_fd = child_fd

        file_stage = True
        file_fd = os.open(filename, _FILE_FLAGS, dir_fd=parent_fd)
        descriptors.append(file_fd)
        before = os.fstat(file_fd)
        _validate_file(before, expected_uid=expected_uid, max_bytes=max_bytes)
        raw = os.read(file_fd, max_bytes + 1)
        after = os.fstat(file_fd)
        if len(raw) != before.st_size or _file_identity(after) != _file_identity(before):
            _fail("UNSTABLE_FILE")
        try:
            visible = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
        except OSError:
            _fail("UNSTABLE_FILE")
        if _file_identity(visible) != _file_identity(after):
            _fail("UNSTABLE_FILE")

        _validate_directory(os.fstat(root_fd), expected_uid=expected_uid, policy=root_policy)
        for visible_parent, name, held_fd, policy in directories:
            try:
                held = os.fstat(held_fd)
                visible = os.stat(name, dir_fd=visible_parent, follow_symlinks=False)
            except OSError:
                _fail("UNSTABLE_FILE")
            _validate_directory(held, expected_uid=expected_uid, policy=policy)
            if _directory_identity(visible) != _directory_identity(held):
                _fail("UNSTABLE_FILE")
        return raw
    except RenderAuthorityRecordError:
        raise
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            _fail("MISSING")
        _fail("UNSAFE_FILE" if file_stage else "UNSAFE_PATH")
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def write_record_once(data_root: Path, directory: str, filename: str, raw: bytes) -> None:
    """Publish ``render-authority/<directory>/<filename>`` (0600) exactly once.

    The bytes are written and fsynced to a private temp file first, then hard-linked to
    the final name, which never replaces anything: a crash or full disk leaves no
    partial record, and an existing record always wins (``FileExistsError``).
    """
    final = _plain_name(filename)
    temp = f".{final}.{secrets.token_hex(8)}.tmp"
    authority_fd = os.open(Path(data_root) / AUTHORITY_DIRECTORY, _DIR_FLAGS)
    try:
        try:
            os.mkdir(_plain_name(directory), 0o700, dir_fd=authority_fd)
        except FileExistsError:
            pass
        directory_fd = os.open(directory, _DIR_FLAGS, dir_fd=authority_fd)
        try:
            fd = os.open(
                temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600, dir_fd=directory_fd)
            try:
                try:
                    view = memoryview(raw)
                    while view:
                        view = view[os.write(fd, view):]
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.link(temp, final, src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
                        follow_symlinks=False)
            finally:
                try:
                    os.unlink(temp, dir_fd=directory_fd)
                except OSError:
                    pass
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        os.close(authority_fd)


def _private_child(parent_fd: int, name: str, expected_uid: int) -> int:
    """Open (creating owner-only when absent) a child that must be a private directory."""
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    try:
        _validate_directory(os.fstat(fd), expected_uid=expected_uid, policy="private")
    except BaseException:
        os.close(fd)
        raise
    return fd


def write_record_replace(data_root: Path, directory: str, filename: str, raw: bytes,
                         *, expected_uid: int) -> None:
    """Publish ``render-authority/<directory>/<filename>`` (0600), replacing any earlier copy.

    Only for a setting the Hub itself owns (``opt-in/unqualified.json``); evidence such as a
    receipt goes through ``write_record_once`` and is never replaced. The authority directory
    and ``directory`` are created owner-only when absent and must already be owner-only when
    present. The bytes are fsynced to a private temp file and one atomic rename publishes
    them, so a crash leaves either the old record or the new one and never a partial.
    """
    final = _plain_name(filename)
    temp = f".{final}.{secrets.token_hex(8)}.tmp"
    descriptors: list[int] = []
    try:
        parent_fd = os.open(Path(data_root), _DIR_FLAGS)
        descriptors.append(parent_fd)
        for name in (AUTHORITY_DIRECTORY, _plain_name(directory)):
            parent_fd = _private_child(parent_fd, name, expected_uid)
            descriptors.append(parent_fd)
        fd = os.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600, dir_fd=parent_fd)
        try:
            try:
                view = memoryview(raw)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.rename(temp, final, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        except BaseException:
            try:
                os.unlink(temp, dir_fd=parent_fd)
            except OSError:
                pass
            raise
        os.fsync(parent_fd)
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
