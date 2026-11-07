"""Descriptor-relative durable inputs for private fleet rendering."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from components.os_metadata import is_os_metadata

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")
_OFFSET_NAME = re.compile(r"[0-9a-f]{16}\.json\Z")
_BUFFER_BYTES = 64 * 1024
_MAX_RECORD_BYTES = 8192
_MAX_I63 = (1 << 63) - 1
_RECORD_BUDGET = _MAX_RECORD_BYTES
_ROOT_NAMES = {"format.json", "store.lock", "index", "uploads", "assets", "published", "_trash"}
_UPLOAD_NAMES = {"head.json", "ranges", "chunks", "assembly", "protections", "changes"}
_RECOVERY_REASONS = {"unknown_write", "unsafe_entry", "missing_record", "integrity", "record_change"}
_RETIRE_BUCKET = "retired"
_RETIRE_AFTER_S = 24 * 3600


class RenderAssetError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class AssetValidationError(RenderAssetError, ValueError):
    def __init__(self, detail: str):
        super().__init__("INVALID_INPUT", detail)


class AssetNotFoundError(RenderAssetError, LookupError):
    def __init__(self, detail: str):
        super().__init__("NOT_FOUND", detail)


class AssetConflictError(RenderAssetError):
    def __init__(self, detail: str):
        super().__init__("UPLOAD_CONFLICT", detail)


class AssetCapacityError(RenderAssetError):
    def __init__(self, detail: str):
        super().__init__("NO_SPACE", detail)


class AssetRecoveryRequiredError(RenderAssetError):
    def __init__(self, _detail: str):
        super().__init__("RECOVERY_REQUIRED", "render upload requires recovery")


class _StreamValidationError(RenderAssetError):
    pass


def _exact_int(value: Any, name: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0) or value > _MAX_I63:
        qualifier = "positive" if positive else "non-negative"
        raise AssetValidationError(f"{name} must be a {qualifier} signed-63-bit integer")
    return value


def _owner_token(owner_scope: Any) -> str:
    if (not isinstance(owner_scope, str) or not owner_scope or owner_scope != owner_scope.strip()
            or len(owner_scope) > 512 or any(ord(char) < 32 for char in owner_scope)
            or any(part in {".", ".."} for part in owner_scope.split("/"))):
        raise RenderAssetError("INVALID_OWNER_SCOPE", "owner scope is invalid")
    try:
        encoded = owner_scope.encode("utf-8")
    except UnicodeError as exc:
        raise RenderAssetError("INVALID_OWNER_SCOPE", "owner scope is invalid") from exc
    return hashlib.sha256(encoded).hexdigest()


def _validated_sha(value: Any, name: str = "sha256") -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RenderAssetError("INVALID_SHA256", "sha256 must be lower-case hex")
    return value


def _validated_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise AssetValidationError(f"{name} is invalid")
    return value


def _upload_id(value: Any) -> str:
    try:
        return _validated_id(value, "upload_id")
    except AssetValidationError as exc:
        raise RenderAssetError("NOT_FOUND", "render upload not found") from exc


def _asset_id(value: Any) -> str:
    try:
        return _validated_id(value, "asset_handle")
    except AssetValidationError as exc:
        raise RenderAssetError("NOT_FOUND", "render asset not found") from exc


def _public_int(value: Any, *, code: str, detail: str, positive: bool = False) -> int:
    try:
        return _exact_int(value, detail, positive=positive)
    except AssetValidationError as exc:
        raise RenderAssetError(code, detail) from exc


def _validated_reference(value: Any) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 512:
        raise RenderAssetError("INVALID_REFERENCE", "reference id is invalid")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise RenderAssetError("INVALID_REFERENCE", "reference id is invalid")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as exc:
        raise RenderAssetError("INVALID_REFERENCE", "reference id is invalid") from exc
    if len(encoded) > 2048:
        raise RenderAssetError("INVALID_REFERENCE", "reference id is invalid")
    return value


def _byte_view(piece: Any) -> memoryview:
    if not isinstance(piece, (bytes, bytearray, memoryview)):
        raise _StreamValidationError("INVALID_STREAM", "stream must yield bytes")
    view = memoryview(piece)
    if not view.contiguous:
        raise _StreamValidationError("INVALID_STREAM", "stream must yield bytes")
    try:
        return view.cast("B")
    except (TypeError, ValueError) as exc:
        raise _StreamValidationError("INVALID_STREAM", "stream must yield bytes") from exc


def _write_all(descriptor: int, data: bytes | memoryview) -> None:
    view = _byte_view(data)
    while view:
        written = os.write(descriptor, view)
        if written < 1:
            raise OSError("short render asset write")
        view = view[written:]


def _iter_stream(stream: Any, length: int) -> Iterator[memoryview]:
    remaining = length
    read = getattr(stream, "read", None)
    if callable(read):
        while remaining:
            view = _byte_view(read(min(_BUFFER_BYTES, remaining)))
            if not view:
                raise _StreamValidationError("STREAM_LENGTH_MISMATCH", "stream length changed")
            if view.nbytes > remaining:
                raise _StreamValidationError("STREAM_LENGTH_MISMATCH", "stream length changed")
            remaining -= view.nbytes
            yield view
        if _byte_view(read(1)):
            raise _StreamValidationError("STREAM_LENGTH_MISMATCH", "stream length changed")
        return
    try:
        iterator = iter(stream)
    except TypeError as exc:
        raise _StreamValidationError("INVALID_STREAM", "stream must yield bytes") from exc
    for piece in iterator:
        view = _byte_view(piece)
        if not view:
            continue
        if view.nbytes > remaining:
            raise _StreamValidationError("STREAM_LENGTH_MISMATCH", "stream length changed")
        remaining -= view.nbytes
        for start in range(0, view.nbytes, _BUFFER_BYTES):
            yield view[start : start + _BUFFER_BYTES]
        if remaining == 0:
            try:
                extra = next(iterator)
            except StopIteration:
                return
            if _byte_view(extra):
                raise _StreamValidationError("STREAM_LENGTH_MISMATCH", "stream length changed")
    if remaining:
        raise _StreamValidationError("STREAM_LENGTH_MISMATCH", "stream length changed")


def _no_float(value: str):
    raise ValueError("floating point JSON is forbidden")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _valid_unicode(value: Any) -> bool:
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeError:
            return False
    if isinstance(value, list):
        return all(_valid_unicode(item) for item in value)
    if isinstance(value, dict):
        return all(_valid_unicode(key) and _valid_unicode(item) for key, item in value.items())
    return True


def _stored_int(value: Any, *, positive: bool = False) -> bool:
    return type(value) is int and (value >= (1 if positive else 0)) and value <= _MAX_I63


def _stored_sha(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _stored_id(value: Any) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _exact_keys(record: dict, keys: set[str]) -> None:
    if set(record) != keys:
        raise ValueError("record keys are not closed")


def _validate_pending(pending: Any, total: int) -> None:
    if not isinstance(pending, dict) or not isinstance(pending.get("kind"), str):
        raise ValueError("invalid pending operation")
    if pending["kind"] == "range":
        _exact_keys(pending, {"kind", "operation_id", "candidate_id", "offset", "length", "phase", "sha256"})
        if not _stored_id(pending["operation_id"]) or not _stored_id(pending["candidate_id"]):
            raise ValueError("invalid pending identity")
        if not _stored_int(pending["offset"]) or not _stored_int(pending["length"], positive=True):
            raise ValueError("invalid pending range")
        if pending["offset"] > total or pending["length"] > total - pending["offset"]:
            raise ValueError("pending range overflow")
        if pending["phase"] not in {"prepared", "verified", "rejected"}:
            raise ValueError("invalid pending phase")
        if pending["phase"] == "verified":
            if not _stored_sha(pending["sha256"]):
                raise ValueError("verified range lacks digest")
        elif pending["sha256"] is not None:
            raise ValueError("unverified range has digest")
        return
    if pending["kind"] == "publication":
        _exact_keys(pending, {"kind", "operation_id", "candidate_id", "phase"})
        if not _stored_id(pending["operation_id"]) or not _stored_id(pending["candidate_id"]):
            raise ValueError("invalid publication identity")
        if pending["phase"] not in {"prepared", "verified"}:
            raise ValueError("invalid publication phase")
        return
    raise ValueError("invalid pending kind")


def _validate_record(record: Any, expected_kind: str) -> None:
    if not isinstance(record, dict):
        raise ValueError("record must be an object")
    if type(record.get("schema")) is not int or record["schema"] != 1 or record.get("kind") != expected_kind:
        raise ValueError("record schema or kind is invalid")
    common_init = {"schema", "kind", "owner_token", "sha256", "size_bytes", "upload_id", "asset_handle"}
    if expected_kind == "format":
        _exact_keys(record, {"schema", "kind", "format", "store_id"})
        if record["format"] != "render-asset-fd-v1" or not _stored_id(record["store_id"]):
            raise ValueError("invalid format record")
    elif expected_kind == "init":
        _exact_keys(record, common_init)
        if not (_stored_sha(record["owner_token"]) and _stored_sha(record["sha256"]) and _stored_int(record["size_bytes"], positive=True) and _stored_id(record["upload_id"]) and _stored_id(record["asset_handle"])):
            raise ValueError("invalid init record")
    elif expected_kind == "upload_head":
        _exact_keys(record, common_init | {"generation", "committed_offset", "state", "pending", "recovery_reason"})
        init_copy = {key: record[key] for key in common_init}
        init_copy["kind"] = "init"
        _validate_record(init_copy, "init")
        if not _stored_int(record["generation"]) or not _stored_int(record["committed_offset"]):
            raise ValueError("invalid head counters")
        if record["committed_offset"] > record["size_bytes"] or record["state"] not in {"incomplete", "published", "recovery_required"}:
            raise ValueError("invalid head state")
        if record["pending"] is not None:
            _validate_pending(record["pending"], record["size_bytes"])
        if record["recovery_reason"] is not None and record["recovery_reason"] not in _RECOVERY_REASONS:
            raise ValueError("invalid recovery reason")
        if record["state"] == "published" and (record["committed_offset"] != record["size_bytes"] or record["pending"] is not None):
            raise ValueError("invalid published head")
    elif expected_kind == "range":
        _exact_keys(record, {"schema", "kind", "owner_token", "upload_id", "operation_id", "candidate_id", "offset", "length", "sha256"})
        if not (_stored_sha(record["owner_token"]) and _stored_id(record["upload_id"]) and _stored_id(record["operation_id"]) and _stored_id(record["candidate_id"]) and _stored_int(record["offset"]) and _stored_int(record["length"], positive=True) and _stored_sha(record["sha256"])):
            raise ValueError("invalid range record")
        if record["offset"] > _MAX_I63 - record["length"]:
            raise ValueError("range overflow")
    elif expected_kind == "asset":
        _exact_keys(record, {"schema", "kind", "owner_token", "upload_id", "asset_handle", "sha256", "size_bytes", "operation_id"})
        if not (_stored_sha(record["owner_token"]) and _stored_id(record["upload_id"]) and _stored_id(record["asset_handle"]) and _stored_sha(record["sha256"]) and _stored_int(record["size_bytes"], positive=True) and _stored_id(record["operation_id"])):
            raise ValueError("invalid asset record")
    elif expected_kind == "protection":
        _exact_keys(record, {"schema", "kind", "owner_token", "upload_id", "asset_handle", "protection_kind", "reference", "generation", "state"})
        if not (_stored_sha(record["owner_token"]) and _stored_id(record["upload_id"]) and _stored_id(record["asset_handle"]) and record["protection_kind"] in {"reference", "reader"} and _stored_int(record["generation"]) and record["state"] in {"active", "released", "unknown"}):
            raise ValueError("invalid protection record")
        _validated_reference(record["reference"])
    elif expected_kind == "record_change":
        keys = {"schema", "kind", "operation_id", "record_kind", "target_name", "parent_device", "parent_inode", "old_generation", "old_sha256", "old_device", "old_inode", "new_generation", "new_sha256", "candidate_id", "candidate_device", "candidate_inode", "archive_id", "archive_device", "archive_inode"}
        _exact_keys(record, keys)
        if not (_stored_id(record["operation_id"]) and record["record_kind"] in {"upload_head", "protection"} and _stored_int(record["parent_device"]) and _stored_int(record["parent_inode"]) and _stored_int(record["new_generation"]) and _stored_sha(record["new_sha256"]) and _stored_id(record["candidate_id"]) and _stored_int(record["candidate_device"]) and _stored_int(record["candidate_inode"]) and _stored_id(record["archive_id"]) and _stored_int(record["archive_device"]) and _stored_int(record["archive_inode"])):
            raise ValueError("invalid record change")
        if record["record_kind"] == "upload_head":
            if record["target_name"] != "head.json":
                raise ValueError("invalid head target")
        elif not isinstance(record["target_name"], str) or re.fullmatch(r"[0-9a-f]{64}\.json", record["target_name"]) is None:
            raise ValueError("invalid protection target")
        old = (record["old_generation"], record["old_sha256"], record["old_device"], record["old_inode"])
        if all(value is None for value in old):
            if record["new_generation"] != 0:
                raise ValueError("invalid creation generation")
        elif any(value is None for value in old) or not (_stored_int(old[0]) and _stored_sha(old[1]) and _stored_int(old[2]) and _stored_int(old[3]) and record["new_generation"] == old[0] + 1):
            raise ValueError("invalid replacement identity")
    else:
        raise ValueError("unknown record kind")


def _decode_record(data: bytes, expected_kind: str) -> dict[str, Any]:
    try:
        if len(data) > _MAX_RECORD_BYTES or not data.endswith(b"\n"):
            raise ValueError("record size or terminator is invalid")
        record = json.loads(data[:-1].decode("utf-8"), object_pairs_hook=_pairs, parse_float=_no_float, parse_constant=_no_float)
        if not _valid_unicode(record):
            raise ValueError("invalid Unicode")
        _validate_record(record, expected_kind)
        if canonical := (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"):
            if canonical != data:
                raise ValueError("record is not canonical")
        return record
    except (AssetValidationError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AssetRecoveryRequiredError("render asset metadata record is invalid") from exc


def _encode_record(record: dict[str, Any]) -> bytes:
    try:
        encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    except (UnicodeError, TypeError, ValueError) as exc:
        raise AssetValidationError("record cannot be encoded") from exc
    _decode_record(encoded, record.get("kind", ""))
    return encoded


def _protection_key(owner_token: str, asset_handle: str, protection_kind: str, reference: str) -> str:
    raw = (json.dumps([owner_token, asset_handle, protection_kind, reference], ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _close_descriptors(descriptors: list[int]) -> None:
    # Pop before close so an explicit close, a failed constructor and a late finalizer cannot close a reused fd.
    while descriptors:
        try:
            os.close(descriptors.pop())
        except OSError:
            pass


@contextmanager
def _scan_entries(descriptor: int) -> Iterator[Iterator[os.DirEntry]]:
    # Filter before validation, emptiness checks, recovery and retirement accounting.
    with os.scandir(descriptor) as iterator:
        yield (entry for entry in iterator if not is_os_metadata(entry.name))


def _known_entries(parent_fd: int, allowed: set[str], *, initializing: bool = False) -> set[str]:
    entries = set()
    with _scan_entries(parent_fd) as iterator:
        for entry in iterator:
            if entry.name not in allowed:
                if initializing:
                    raise AssetValidationError("existing render asset root has another format")
                raise AssetRecoveryRequiredError("render asset directory has an unexpected entry")
            entries.add(entry.name)
    return entries


@dataclass
class _UploadDirs:
    upload_id: str
    path: Path
    upload: int
    ranges: int
    chunks: int
    assembly: int
    protections: int
    changes: int

    def close(self) -> None:
        for descriptor in (self.changes, self.protections, self.assembly, self.chunks, self.ranges, self.upload):
            os.close(descriptor)


class _ReaderLease:
    def __init__(self, store: "RenderAssetStore", owner_token: str, asset_handle: str) -> None:
        self._store = store
        self._owner_token = owner_token
        self._asset_handle = asset_handle
        self._lease_id: str | None = None
        self._file = None
        self.sha256 = ""
        self.size_bytes = 0

    def __enter__(self) -> dict[str, Any]:
        self._store._enter_reader(self)
        return {
            "asset_handle": self._asset_handle,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "stream": self._file,
        }

    def read(self, size: int = -1) -> bytes:
        if self._file is None:
            raise ValueError("reader lease is not active")
        return self._file.read(size)

    def __exit__(self, exc_type, exc, traceback) -> None:
        close_error = None
        try:
            if self._file is not None:
                self._file.close()
        except BaseException as caught:
            close_error = caught
        finally:
            self._file = None
        try:
            if self._lease_id is not None and not self._store._closed:
                if close_error is None:
                    self._store._release_reader(self._owner_token, self._asset_handle, self._lease_id)
                else:
                    self._store._unknown_reader(self._owner_token, self._asset_handle, self._lease_id)
        finally:
            self._lease_id = None
            self._store._forget_reader(self)
        if close_error is not None:
            raise close_error

    def _close_for_store_shutdown(self) -> None:
        try:
            if self._file is not None:
                self._file.close()
        finally:
            self._file = None


class RenderAssetStore:
    _root_locks_guard = threading.Lock()
    _root_locks: dict[tuple[int, int], threading.RLock] = {}

    def __init__(self, data_root: str | Path, *, max_chunk_bytes: int, max_asset_bytes: int, reserve_bytes: int, free_bytes: Callable[[Path], int]) -> None:
        for name, value in (("max_chunk_bytes", max_chunk_bytes), ("max_asset_bytes", max_asset_bytes), ("reserve_bytes", reserve_bytes)):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if max_chunk_bytes == 0 or max_asset_bytes == 0:
            raise ValueError("asset and chunk limits must be positive")
        if not callable(free_bytes):
            raise TypeError("free_bytes must be callable")
        self.max_chunk_bytes = max_chunk_bytes
        self.max_asset_bytes = max_asset_bytes
        self.reserve_bytes = reserve_bytes
        self.free_bytes = free_bytes
        self.root = Path(data_root).absolute()
        self._parent_path = self.root.parent
        self._lock = threading.RLock()
        self._open_readers: set[_ReaderLease] = set()
        self._closed = False
        self._held: list[int] = []
        # The callback holds the list, never self, so an abandoned store releases its fds without keeping the store alive.
        self._finalizer = weakref.finalize(self, _close_descriptors, self._held)
        self._initializing = True
        try:
            self._parent_fd = self._hold(self._ensure_directory_chain(self._parent_path))
            self._reject_symlink_chain(self._parent_path)
            self._parent_identity = _identity(os.fstat(self._parent_fd))
            parent_visible = os.stat(self._parent_path, follow_symlinks=False)
            if not stat.S_ISDIR(parent_visible.st_mode) or _identity(parent_visible) != self._parent_identity:
                raise AssetRecoveryRequiredError("render asset parent identity changed")
            try:
                self._root_fd = self._hold(os.open(self.root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._parent_fd))
                fresh_root = False
            except FileNotFoundError:
                os.mkdir(self.root.name, 0o700, dir_fd=self._parent_fd)
                os.fsync(self._parent_fd)
                self._root_fd = self._hold(os.open(self.root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._parent_fd))
                fresh_root = True
            self._root_identity = _identity(os.fstat(self._root_fd))
            entries = _known_entries(self._root_fd, _ROOT_NAMES, initializing=True)
            if entries and "format.json" not in entries:
                raise AssetValidationError("existing render asset root has another format")
            if entries and entries != _ROOT_NAMES:
                raise AssetValidationError("render asset root contains unexpected entries")
            if not entries:
                fresh_root = True
                self._initialize_root()
            self._lock_fd = self._hold(os.open("store.lock", os.O_RDWR | os.O_NOFOLLOW, dir_fd=self._root_fd))
            lock_info = os.fstat(self._lock_fd)
            if not stat.S_ISREG(lock_info.st_mode):
                raise AssetValidationError("render asset lock is unsafe")
            self._lock_identity = _identity(lock_info)
            with self._root_locks_guard:
                self._root_thread_lock = self._root_locks.setdefault(self._root_identity, threading.RLock())
            with self._root_guard():
                self._index_fd = self._open_held_dir(self._root_fd, "index")
                self._uploads_fd = self._open_held_dir(self._root_fd, "uploads")
                self._assets_fd = self._open_held_dir(self._root_fd, "assets")
                self._published_fd = self._open_held_dir(self._root_fd, "published")
                self._trash_fd = self._open_held_dir(self._root_fd, "_trash")
                self._history_fd = self._open_held_dir(self._trash_fd, "metadata-history")
                self._fixed_identities = {
                    self._index_fd: _identity(os.fstat(self._index_fd)),
                    self._uploads_fd: _identity(os.fstat(self._uploads_fd)),
                    self._assets_fd: _identity(os.fstat(self._assets_fd)),
                    self._published_fd: _identity(os.fstat(self._published_fd)),
                    self._trash_fd: _identity(os.fstat(self._trash_fd)),
                    self._history_fd: _identity(os.fstat(self._history_fd)),
                }
                format_record, _, _ = self._read_record(self._root_fd, "format.json", "format")
                self.store_id = format_record["store_id"]
                self._assert_layout()
            self._initializing = False
        except (AssetValidationError, AssetRecoveryRequiredError, OSError) as exc:
            self._close_held()
            self._closed = True
            raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe") from exc
        except BaseException:
            self._close_held()
            self._closed = True
            raise

    def _hold(self, descriptor: int) -> int:
        self._held.append(descriptor)
        return descriptor

    def _close_held(self) -> list[BaseException]:
        errors = []
        while self._held:
            descriptor = self._held.pop()
            try:
                os.close(descriptor)
            except BaseException as exc:
                errors.append(exc)
        return errors

    @staticmethod
    def _ensure_directory_chain(path: Path) -> int:
        descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        child = None
        keep_descriptor = False
        try:
            for part in path.parts[1:]:
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                except FileNotFoundError:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                child_info = os.fstat(child)
                visible = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                if not stat.S_ISDIR(child_info.st_mode) or _identity(child_info) != _identity(visible):
                    raise AssetValidationError("render asset path is unsafe")
                os.close(descriptor)
                descriptor = child
                child = None
            keep_descriptor = True
            return descriptor
        except OSError as exc:
            raise AssetValidationError("render asset path is unsafe") from exc
        except BaseException:
            raise
        finally:
            if child is not None:
                os.close(child)
            if not keep_descriptor:
                os.close(descriptor)

    @staticmethod
    def _reject_symlink_chain(path: Path) -> None:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise AssetValidationError("render asset parent path is unsafe")

    def _initialize_root(self) -> None:
        lock_fd = os.open("store.lock", os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self._root_fd)
        try:
            os.fsync(lock_fd)
        finally:
            os.close(lock_fd)
        os.fsync(self._root_fd)
        for name in ("index", "uploads", "assets", "published", "_trash"):
            os.mkdir(name, 0o700, dir_fd=self._root_fd)
            os.fsync(self._root_fd)
        trash_fd = os.open("_trash", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._root_fd)
        try:
            os.mkdir("metadata-history", 0o700, dir_fd=trash_fd)
            os.fsync(trash_fd)
        finally:
            os.close(trash_fd)
        record = {"schema": 1, "kind": "format", "format": "render-asset-fd-v1", "store_id": uuid.uuid4().hex}
        self._create_file(self._root_fd, "format.json", _encode_record(record))
        os.fsync(self._root_fd)

    def _open_held_dir(self, parent_fd: int, name: str) -> int:
        return self._hold(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd))

    @contextmanager
    def _root_guard(self):
        lock = getattr(self, "_root_thread_lock", threading.RLock())
        with lock:
            if hasattr(self, "_lock_fd"):
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if hasattr(self, "_lock_fd"):
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)

    @contextmanager
    def _exclusive(self):
        with self._root_thread_lock:
            with self._lock:
                if self._closed:
                    raise RenderAssetError("STORE_CLOSED", "render asset store is closed")
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
                try:
                    self._assert_layout()
                    yield
                finally:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)

    def _assert_layout(self) -> None:
        try:
            if _identity(self._parent_path.lstat()) != self._parent_identity or _identity(self.root.lstat()) != self._root_identity:
                raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe")
            lock_info = os.stat("store.lock", dir_fd=self._root_fd, follow_symlinks=False)
            if not stat.S_ISREG(lock_info.st_mode) or _identity(lock_info) != self._lock_identity:
                raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe")
            for descriptor, expected in getattr(self, "_fixed_identities", {}).items():
                if _identity(os.fstat(descriptor)) != expected:
                    raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe")
            for name, descriptor in (("index", getattr(self, "_index_fd", -1)), ("uploads", getattr(self, "_uploads_fd", -1)), ("assets", getattr(self, "_assets_fd", -1)), ("published", getattr(self, "_published_fd", -1)), ("_trash", getattr(self, "_trash_fd", -1))):
                if descriptor >= 0 and _identity(os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)) != _identity(os.fstat(descriptor)):
                    raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe")
        except RenderAssetError:
            raise
        except OSError as exc:
            raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe") from exc

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            errors = []
            for reader in tuple(self._open_readers):
                try:
                    reader._close_for_store_shutdown()
                except BaseException as exc:
                    errors.append(exc)
            self._open_readers.clear()
            errors.extend(self._close_held())
            if errors:
                raise errors[0]

    def _measure(self, needed: int) -> None:
        try:
            free = self.free_bytes(self.root)
        except Exception as exc:
            raise AssetCapacityError("free disk space is unavailable") from exc
        if type(free) is not int or free < 0 or free > _MAX_I63:
            raise AssetCapacityError("render asset does not fit above reserve")
        if free < self.reserve_bytes + needed:
            raise AssetCapacityError("render asset does not fit above reserve")

    @staticmethod
    def _create_file(parent_fd: int, name: str, data: bytes) -> tuple[int, int]:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
        try:
            _write_all(descriptor, data)
            os.fsync(descriptor)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise AssetRecoveryRequiredError("metadata candidate is not regular")
            return _identity(info)
        finally:
            os.close(descriptor)

    @staticmethod
    def _read_bytes(parent_fd: int, name: str) -> tuple[bytes, tuple[int, int]]:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise AssetRecoveryRequiredError("metadata leaf is not regular")
            pieces = []
            total = 0
            while total <= _MAX_RECORD_BYTES:
                piece = os.read(descriptor, min(4096, _MAX_RECORD_BYTES + 1 - total))
                if not piece:
                    break
                pieces.append(piece)
                total += len(piece)
            visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if _identity(visible) != _identity(info):
                raise AssetRecoveryRequiredError("opened metadata leaf changed")
            return b"".join(pieces), _identity(info)
        except BaseException:
            raise
        finally:
            os.close(descriptor)

    def _read_record(self, parent_fd: int, name: str, kind: str) -> tuple[dict[str, Any], bytes, tuple[int, int]]:
        try:
            raw, identity = self._read_bytes(parent_fd, name)
        except (OSError, AssetRecoveryRequiredError) as exc:
            raise AssetRecoveryRequiredError("render asset metadata is unavailable") from exc
        return _decode_record(raw, kind), raw, identity

    @staticmethod
    def _entry_exists(parent_fd: int, name: str) -> bool:
        try:
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True

    def _publish_immutable(self, parent_fd: int, target: str, record: dict[str, Any], candidate_id: str | None = None) -> None:
        data = _encode_record(record)
        candidate_id = candidate_id or uuid.uuid4().hex
        candidate = f"candidate-{candidate_id}.json"
        candidate_missing = not self._entry_exists(parent_fd, candidate)
        target_missing = not self._entry_exists(parent_fd, target)
        if candidate_missing or target_missing:
            self._measure(2 * _RECORD_BUDGET)
        if candidate_missing:
            self._create_file(parent_fd, candidate, data)
        candidate_raw, candidate_identity = self._read_bytes(parent_fd, candidate)
        if candidate_raw != data:
            raise AssetRecoveryRequiredError("immutable candidate conflicts")
        try:
            os.link(candidate, target, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
        except FileExistsError:
            pass
        target_raw, target_identity = self._read_bytes(parent_fd, target)
        if target_raw != data or target_identity != candidate_identity:
            raise AssetRecoveryRequiredError("immutable target has an unexpected winner")
        os.fsync(parent_fd)

    def _open_archive(self, archive_id: str, *, create: bool) -> int:
        if create:
            os.mkdir(archive_id, 0o700, dir_fd=self._history_fd)
            os.fsync(self._history_fd)
        return os.open(archive_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._history_fd)

    def _change_record(self, dirs: _UploadDirs, parent_fd: int, target: str, kind: str, new_record: dict[str, Any]) -> None:
        self._recover_changes(dirs)
        self._measure(4 * _RECORD_BUDGET)
        parent_info = os.fstat(parent_fd)
        old_record = old_raw = old_identity = None
        if self._entry_exists(parent_fd, target):
            old_record, old_raw, old_identity = self._read_record(parent_fd, target, kind)
            if new_record["generation"] != old_record["generation"] + 1:
                raise AssetRecoveryRequiredError("record generation is not contiguous")
        elif new_record["generation"] != 0:
            raise AssetRecoveryRequiredError("replacement record is missing")
        operation_id, candidate_id, archive_id = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
        archive_fd = self._open_archive(archive_id, create=True)
        try:
            archive_info = os.fstat(archive_fd)
            candidate_name = f"candidate-{candidate_id}.json"
            new_raw = _encode_record(new_record)
            candidate_identity = self._create_file(dirs.changes, candidate_name, new_raw)
            marker = {
                "schema": 1,
                "kind": "record_change",
                "operation_id": operation_id,
                "record_kind": kind,
                "target_name": target,
                "parent_device": parent_info.st_dev,
                "parent_inode": parent_info.st_ino,
                "old_generation": None if old_record is None else old_record["generation"],
                "old_sha256": None if old_raw is None else hashlib.sha256(old_raw).hexdigest(),
                "old_device": None if old_identity is None else old_identity[0],
                "old_inode": None if old_identity is None else old_identity[1],
                "new_generation": new_record["generation"],
                "new_sha256": hashlib.sha256(new_raw).hexdigest(),
                "candidate_id": candidate_id,
                "candidate_device": candidate_identity[0],
                "candidate_inode": candidate_identity[1],
                "archive_id": archive_id,
                "archive_device": archive_info.st_dev,
                "archive_inode": archive_info.st_ino,
            }
            marker_name = f"change-{operation_id}.json"
            self._create_file(dirs.changes, marker_name, _encode_record(marker))
            os.fsync(dirs.changes)
            if old_record is not None:
                os.rename(target, "prior.json", src_dir_fd=parent_fd, dst_dir_fd=archive_fd)
                prior, prior_raw, prior_identity = self._read_record(archive_fd, "prior.json", kind)
                if prior_identity != old_identity or prior_raw != old_raw or prior["generation"] != old_record["generation"]:
                    raise AssetRecoveryRequiredError("moved prior record changed")
                os.fsync(parent_fd)
                os.fsync(archive_fd)
            self._complete_change(dirs, marker_name, marker, archive_fd)
        finally:
            os.close(archive_fd)

    def _complete_change(self, dirs: _UploadDirs, marker_name: str, marker: dict[str, Any], archive_fd: int) -> None:
        parent_fd = dirs.upload if marker["record_kind"] == "upload_head" else dirs.protections
        if marker_name != f"change-{marker['operation_id']}.json":
            raise AssetRecoveryRequiredError("record marker name does not match its operation")
        parent_info = os.fstat(parent_fd)
        if _identity(parent_info) != (marker["parent_device"], marker["parent_inode"]):
            raise AssetRecoveryRequiredError("record destination identity changed")
        candidate_name = f"candidate-{marker['candidate_id']}.json"
        candidate_parent = dirs.changes
        if not self._entry_exists(candidate_parent, candidate_name):
            candidate_name = "candidate.json"
            candidate_parent = archive_fd
        candidate_raw, candidate_identity = self._read_bytes(candidate_parent, candidate_name)
        if hashlib.sha256(candidate_raw).hexdigest() != marker["new_sha256"] or candidate_identity != (marker["candidate_device"], marker["candidate_inode"]):
            raise AssetRecoveryRequiredError("record candidate identity changed")
        new_record = _decode_record(candidate_raw, marker["record_kind"])
        if new_record["generation"] != marker["new_generation"]:
            raise AssetRecoveryRequiredError("record candidate generation changed")
        if marker["record_kind"] == "upload_head":
            if new_record["upload_id"] != dirs.upload_id:
                raise AssetRecoveryRequiredError("head candidate upload identity changed")
            self._validate_head_init(new_record)
        else:
            expected_target = f"{_protection_key(new_record['owner_token'], new_record['asset_handle'], new_record['protection_kind'], new_record['reference'])}.json"
            head = self._validated_head(dirs)
            fields = ("owner_token", "upload_id", "asset_handle")
            if any(new_record[field] != head[field] for field in fields) or marker["target_name"] != expected_target:
                raise AssetRecoveryRequiredError("protection candidate identity changed")
        archive_info = os.fstat(archive_fd)
        if _identity(archive_info) != (marker["archive_device"], marker["archive_inode"]):
            raise AssetRecoveryRequiredError("record archive identity changed")
        if marker["old_generation"] is None:
            if self._entry_exists(archive_fd, "prior.json"):
                raise AssetRecoveryRequiredError("creation archive has unexpected prior")
        else:
            prior, prior_raw, prior_identity = self._read_record(archive_fd, "prior.json", marker["record_kind"])
            if prior["generation"] != marker["old_generation"] or hashlib.sha256(prior_raw).hexdigest() != marker["old_sha256"] or prior_identity != (marker["old_device"], marker["old_inode"]):
                raise AssetRecoveryRequiredError("prior record identity changed")
            os.fsync(parent_fd)
            os.fsync(archive_fd)
        if self._entry_exists(parent_fd, marker["target_name"]):
            target_raw, target_identity = self._read_bytes(parent_fd, marker["target_name"])
            if target_identity != candidate_identity or hashlib.sha256(target_raw).hexdigest() != marker["new_sha256"]:
                raise AssetRecoveryRequiredError("record target has an unexpected winner")
        else:
            self._measure(_RECORD_BUDGET)
            os.link(candidate_name, marker["target_name"], src_dir_fd=candidate_parent, dst_dir_fd=parent_fd, follow_symlinks=False)
        target_record, target_raw, target_identity = self._read_record(parent_fd, marker["target_name"], marker["record_kind"])
        if target_identity != candidate_identity or target_record["generation"] != marker["new_generation"] or hashlib.sha256(target_raw).hexdigest() != marker["new_sha256"]:
            raise AssetRecoveryRequiredError("published record identity changed")
        os.fsync(parent_fd)
        if candidate_parent == dirs.changes:
            os.rename(candidate_name, "candidate.json", src_dir_fd=dirs.changes, dst_dir_fd=archive_fd)
            os.fsync(dirs.changes)
            os.fsync(archive_fd)
        if self._entry_exists(dirs.changes, marker_name):
            os.rename(marker_name, "marker.json", src_dir_fd=dirs.changes, dst_dir_fd=archive_fd)
            os.fsync(dirs.changes)
            os.fsync(archive_fd)

    def _recover_changes(self, dirs: _UploadDirs) -> None:
        with _scan_entries(dirs.changes) as iterator:
            for entry in iterator:
                if re.fullmatch(r"candidate-[0-9a-f]{32}\.json", entry.name):
                    continue
                if re.fullmatch(r"change-[0-9a-f]{32}\.json", entry.name) is None:
                    raise AssetRecoveryRequiredError("upload has unknown change evidence")
                marker, _, _ = self._read_record(dirs.changes, entry.name, "record_change")
                archive_fd = self._open_archive(marker["archive_id"], create=False)
                try:
                    self._complete_change(dirs, entry.name, marker, archive_fd)
                finally:
                    os.close(archive_fd)
        with _scan_entries(dirs.changes) as iterator:
            if next(iterator, None) is not None:
                raise AssetRecoveryRequiredError("upload has unresolved change evidence")

    @staticmethod
    def _upload_is_pristine(dirs: _UploadDirs) -> bool:
        for descriptor in (dirs.ranges, dirs.chunks, dirs.assembly, dirs.protections, dirs.changes):
            with _scan_entries(descriptor) as iterator:
                if next(iterator, None) is not None:
                    return False
        return True

    def _ensure_upload(self, init: dict[str, Any]) -> _UploadDirs:
        upload_id = init["upload_id"]
        created = False
        try:
            upload_fd = os.open(upload_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._uploads_fd)
        except FileNotFoundError:
            os.mkdir(upload_id, 0o700, dir_fd=self._uploads_fd)
            os.fsync(self._uploads_fd)
            upload_fd = os.open(upload_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._uploads_fd)
            created = True
        descriptors = {}
        try:
            entries = _known_entries(upload_fd, _UPLOAD_NAMES)
            for name in ("ranges", "chunks", "assembly", "protections", "changes"):
                if name not in entries:
                    os.mkdir(name, 0o700, dir_fd=upload_fd)
                    os.fsync(upload_fd)
                descriptors[name] = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=upload_fd)
            dirs = _UploadDirs(upload_id, self.root / "uploads" / upload_id, upload_fd, descriptors["ranges"], descriptors["chunks"], descriptors["assembly"], descriptors["protections"], descriptors["changes"])
            self._recover_changes(dirs)
            if not self._entry_exists(upload_fd, "head.json"):
                if not created and not self._upload_is_pristine(dirs):
                    raise AssetRecoveryRequiredError("authoritative upload head is missing")
                head = {**init, "kind": "upload_head", "generation": 0, "committed_offset": 0, "state": "incomplete", "pending": None, "recovery_reason": None}
                self._change_record(dirs, upload_fd, "head.json", "upload_head", head)
            return dirs
        except BaseException:
            for descriptor in descriptors.values():
                os.close(descriptor)
            os.close(upload_fd)
            raise

    def _find_init_candidate(self, owner: str, sha256: str, size: int) -> dict[str, Any] | None:
        found = None
        with _scan_entries(self._index_fd) as iterator:
            for entry in iterator:
                if not entry.name.startswith("candidate-") or not entry.name.endswith(".json"):
                    continue
                try:
                    record, _, _ = self._read_record(self._index_fd, entry.name, "init")
                except AssetRecoveryRequiredError:
                    continue
                if (record["owner_token"], record["sha256"], record["size_bytes"]) == (owner, sha256, size):
                    if found is not None and (record["upload_id"], record["asset_handle"]) != (found["upload_id"], found["asset_handle"]):
                        raise AssetRecoveryRequiredError("multiple init candidates conflict")
                    found = record
        return found

    @staticmethod
    def _upload_descriptor(head: dict[str, Any]) -> dict[str, Any]:
        state = head["state"]
        return {
            "upload_id": head["upload_id"],
            "sha256": head["sha256"],
            "size_bytes": head["size_bytes"],
            "committed_offset": head["committed_offset"],
            "state": state,
            "asset_handle": head["asset_handle"] if state == "published" else None,
        }

    @staticmethod
    def _asset_descriptor(asset: dict[str, Any]) -> dict[str, Any]:
        return {"asset_handle": asset["asset_handle"], "sha256": asset["sha256"], "size_bytes": asset["size_bytes"]}

    def _validate_head_init(self, head: dict[str, Any]) -> None:
        target = f"{head['owner_token']}-{head['sha256']}.json"
        init, _, _ = self._read_record(self._index_fd, target, "init")
        fields = ("owner_token", "sha256", "size_bytes", "upload_id", "asset_handle")
        if any(head[field] != init[field] for field in fields):
            raise AssetRecoveryRequiredError("upload head does not match its init reservation")

    def _validated_head(self, dirs: _UploadDirs) -> dict[str, Any]:
        head, _, _ = self._read_record(dirs.upload, "head.json", "upload_head")
        if head["upload_id"] != dirs.upload_id:
            raise AssetRecoveryRequiredError("upload head identity changed")
        self._validate_head_init(head)
        return head

    def _head(self, dirs: _UploadDirs) -> dict[str, Any]:
        self._recover_changes(dirs)
        return self._validated_head(dirs)

    def _set_head(self, dirs: _UploadDirs, old: dict[str, Any], **changes) -> dict[str, Any]:
        new = {**old, **changes, "generation": old["generation"] + 1}
        self._change_record(dirs, dirs.upload, "head.json", "upload_head", new)
        return new

    def _mark_recovery(self, dirs: _UploadDirs, head: dict[str, Any], reason: str) -> dict[str, Any]:
        return self._set_head(dirs, head, state="recovery_required", pending=None, recovery_reason=reason)

    def upload_by_digest(
        self, owner_scope: str, sha256: str, size_bytes: int,
    ) -> dict[str, Any]:
        """Return an existing scoped upload without creating or recovering anything."""
        owner = _owner_token(owner_scope)
        sha256 = _validated_sha(sha256)
        size_bytes = _public_int(
            size_bytes, code="INVALID_SIZE", detail="size_bytes must be positive", positive=True)
        with self._exclusive():
            target = f"{owner}-{sha256}.json"
            if not self._entry_exists(self._index_fd, target):
                raise AssetNotFoundError("render upload not found")
            try:
                init, _, _ = self._read_record(self._index_fd, target, "init")
                if init["size_bytes"] != size_bytes:
                    raise AssetConflictError("content length changed")
                upload_fd = os.open(
                    init["upload_id"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=self._uploads_fd,
                )
                try:
                    head, _, _ = self._read_record(upload_fd, "head.json", "upload_head")
                finally:
                    os.close(upload_fd)
                fields = ("owner_token", "sha256", "size_bytes", "upload_id", "asset_handle")
                if any(head[field] != init[field] for field in fields):
                    raise AssetRecoveryRequiredError("upload head does not match its reservation")
                return self._upload_descriptor(head)
            except (AssetConflictError, AssetRecoveryRequiredError):
                raise
            except FileNotFoundError as exc:
                raise AssetRecoveryRequiredError("upload evidence is unavailable") from exc
            except OSError as exc:
                raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe") from exc

    def begin_upload(self, owner_scope: str, sha256: str, size_bytes: int) -> dict[str, Any]:
        owner = _owner_token(owner_scope)
        sha256 = _validated_sha(sha256)
        size_bytes = _public_int(size_bytes, code="INVALID_SIZE", detail="size_bytes must be positive", positive=True)
        if size_bytes > self.max_asset_bytes:
            raise RenderAssetError("ASSET_TOO_LARGE", "render asset exceeds configured limit")
        with self._exclusive():
            self._measure(4 * _RECORD_BUDGET)
            target = f"{owner}-{sha256}.json"
            try:
                target_exists = self._entry_exists(self._index_fd, target)
                init = self._read_record(self._index_fd, target, "init")[0] if target_exists else None
            except (OSError, AssetRecoveryRequiredError) as exc:
                raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe") from exc
            if init is not None:
                if init["owner_token"] != owner or init["sha256"] != sha256:
                    raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe")
                if init["size_bytes"] != size_bytes:
                    raise RenderAssetError("UPLOAD_CONFLICT", "content length changed")
            else:
                init = self._find_init_candidate(owner, sha256, size_bytes)
                if init is None:
                    init = {"schema": 1, "kind": "init", "owner_token": owner, "sha256": sha256, "size_bytes": size_bytes, "upload_id": uuid.uuid4().hex, "asset_handle": uuid.uuid4().hex}
                self._publish_immutable(self._index_fd, target, init)
            dirs = self._ensure_upload(init)
            try:
                head = self._resume_pending(dirs, self._head(dirs))
                return self._upload_descriptor(head)
            finally:
                dirs.close()

    def _owned_init_for_upload(self, owner: str, upload_id: str, sha256: str | None = None) -> dict[str, Any] | None:
        expected = re.compile(rf"{owner}-[0-9a-f]{{64}}\.json\Z")
        names = [f"{owner}-{sha256}.json"] if sha256 is not None else []
        if sha256 is None:
            with _scan_entries(self._index_fd) as entries:
                names = [entry.name for entry in entries if expected.fullmatch(entry.name)]
        found = None
        for name in names:
            if not self._entry_exists(self._index_fd, name):
                continue
            try:
                init, _, _ = self._read_record(self._index_fd, name, "init")
            except AssetRecoveryRequiredError as exc:
                raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe") from exc
            if name != f"{init['owner_token']}-{init['sha256']}.json":
                raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe")
            if init["owner_token"] == owner and init["upload_id"] == upload_id:
                if found is not None and found != init:
                    raise AssetRecoveryRequiredError("multiple upload reservations conflict")
                found = init
        return found

    def _open_owned_upload(self, owner: str, upload_id: str) -> _UploadDirs:
        upload_id = _upload_id(upload_id)
        try:
            upload_fd = os.open(upload_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._uploads_fd)
        except FileNotFoundError as exc:
            raise AssetNotFoundError("render upload not found") from exc
        except OSError as exc:
            raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe") from exc
        descriptors = {}
        try:
            for name in ("ranges", "chunks", "assembly", "protections", "changes"):
                descriptors[name] = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=upload_fd)
            dirs = _UploadDirs(upload_id, self.root / "uploads" / upload_id, upload_fd, descriptors["ranges"], descriptors["chunks"], descriptors["assembly"], descriptors["protections"], descriptors["changes"])
            with _scan_entries(dirs.changes) as entries:
                for entry in entries:
                    if re.fullmatch(r"candidate-[0-9a-f]{32}\.json", entry.name):
                        continue
                    if re.fullmatch(r"change-[0-9a-f]{32}\.json", entry.name) is None:
                        raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe")
                    try:
                        self._read_record(dirs.changes, entry.name, "record_change")
                    except AssetRecoveryRequiredError as exc:
                        raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe") from exc
            if self._entry_exists(dirs.upload, "head.json"):
                try:
                    unscoped_head, _, _ = self._read_record(dirs.upload, "head.json", "upload_head")
                except AssetRecoveryRequiredError as exc:
                    raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe") from exc
                if unscoped_head["upload_id"] != upload_id:
                    raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe")
                if unscoped_head["owner_token"] == owner:
                    self._validate_head_init(unscoped_head)
                elif self._owned_init_for_upload(owner, upload_id, unscoped_head["sha256"]) is not None:
                    raise AssetRecoveryRequiredError("upload head owner conflicts with its reservation")
                else:
                    raise AssetNotFoundError("render upload not found")
            elif self._owned_init_for_upload(owner, upload_id) is None:
                raise AssetNotFoundError("render upload not found")
            head = self._head(dirs)
            if head["owner_token"] != owner:
                raise AssetRecoveryRequiredError("recovered upload owner conflicts with its reservation")
            return dirs
        except BaseException as exc:
            for descriptor in descriptors.values():
                os.close(descriptor)
            os.close(upload_fd)
            if isinstance(exc, OSError):
                raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe") from exc
            raise

    def upload_status(self, owner_scope: str, upload_id: str) -> dict[str, Any]:
        owner = _owner_token(owner_scope)
        upload_id = _upload_id(upload_id)
        with self._exclusive():
            dirs = self._open_owned_upload(owner, upload_id)
            try:
                head = self._resume_pending(dirs, self._head(dirs))
                self._validate_range_records(dirs, head)
                return self._upload_descriptor(head)
            finally:
                dirs.close()

    def _range_target(self, offset: int) -> str:
        return f"{offset:016x}.json"

    def _range_record(self, dirs: _UploadDirs, offset: int) -> dict[str, Any]:
        record, _, _ = self._read_record(dirs.ranges, self._range_target(offset), "range")
        if record["upload_id"] != dirs.upload_id or record["offset"] != offset:
            raise AssetRecoveryRequiredError("range record identity changed")
        return record

    def _validate_range_records(self, dirs: _UploadDirs, head: dict[str, Any]) -> None:
        offset = 0
        while offset < head["committed_offset"]:
            record = self._range_record(dirs, offset)
            if record["owner_token"] != head["owner_token"] or record["length"] > head["committed_offset"] - offset:
                raise AssetRecoveryRequiredError("range record is inconsistent")
            offset += record["length"]
        if offset != head["committed_offset"]:
            raise AssetRecoveryRequiredError("range records are not contiguous")

    def _create_payload(self, parent_fd: int, name: str, stream: Any, length: int) -> str:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
        hasher = hashlib.sha256()
        try:
            for piece in _iter_stream(stream, length):
                hasher.update(piece)
                _write_all(descriptor, piece)
            os.fsync(descriptor)
        except BaseException:
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            raise
        os.close(descriptor)
        os.fsync(parent_fd)
        return hasher.hexdigest()

    def _verify_blob(self, parent_fd: int, name: str, sha256: str, size: int):
        descriptor = None
        handle = None
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise AssetRecoveryRequiredError("payload is not regular")
            visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if _identity(info) != _identity(visible):
                raise AssetRecoveryRequiredError("opened payload changed")
            handle = os.fdopen(descriptor, "rb")
            descriptor = None
            actual = hashlib.sha256()
            total = 0
            while piece := handle.read(_BUFFER_BYTES):
                actual.update(piece)
                total += len(piece)
            if total != size or actual.hexdigest() != sha256:
                raise AssetRecoveryRequiredError("payload failed verification")
            handle.seek(0)
            return handle
        except (OSError, AssetRecoveryRequiredError) as exc:
            if handle is not None:
                handle.close()
            if descriptor is not None:
                os.close(descriptor)
            raise AssetRecoveryRequiredError("payload is unavailable") from exc
        except BaseException:
            if handle is not None:
                handle.close()
            if descriptor is not None:
                os.close(descriptor)
            raise

    def append_chunk(self, owner_scope: str, upload_id: str, offset: int, stream: Any, length: int) -> int:
        owner = _owner_token(owner_scope)
        upload_id = _upload_id(upload_id)
        offset = _public_int(offset, code="INVALID_OFFSET", detail="offset must be a non-negative integer")
        length = _public_int(length, code="INVALID_LENGTH", detail="chunk length must be positive", positive=True)
        if length > self.max_chunk_bytes:
            raise RenderAssetError("CHUNK_TOO_LARGE", "chunk exceeds configured limit")
        with self._exclusive():
            dirs = self._open_owned_upload(owner, upload_id)
            try:
                head = self._resume_pending(dirs, self._head(dirs))
                if head["state"] == "recovery_required":
                    raise AssetRecoveryRequiredError("render upload requires recovery")
                if head["state"] != "incomplete":
                    raise RenderAssetError("RANGE_CONFLICT", "published upload cannot change")
                if offset < head["committed_offset"]:
                    record = self._range_record(dirs, offset)
                    if record["length"] != length:
                        raise RenderAssetError("RANGE_CONFLICT", "accepted range bytes changed")
                    replay = hashlib.sha256()
                    for piece in _iter_stream(stream, length):
                        replay.update(piece)
                    if replay.hexdigest() != record["sha256"]:
                        raise RenderAssetError("RANGE_CONFLICT", "accepted range bytes changed")
                    return offset + length
                if offset != head["committed_offset"]:
                    raise RenderAssetError("NONCONTIGUOUS_OFFSET", "chunk is not contiguous")
                if offset > head["size_bytes"] or length > head["size_bytes"] - offset:
                    raise RenderAssetError("INVALID_LENGTH", "chunk exceeds declared asset length")
                target = self._range_target(offset)
                if self._entry_exists(dirs.ranges, target):
                    self._mark_recovery(dirs, head, "unsafe_entry")
                    raise AssetRecoveryRequiredError("unexpected range winner")
                self._measure((head["size_bytes"] - offset) + head["size_bytes"] + 10 * _RECORD_BUDGET)
                operation_id, candidate_id = uuid.uuid4().hex, uuid.uuid4().hex
                pending = {"kind": "range", "operation_id": operation_id, "candidate_id": candidate_id, "offset": offset, "length": length, "phase": "prepared", "sha256": None}
                head = self._set_head(dirs, head, pending=pending)
                payload_name = f"{candidate_id}.bin"
                try:
                    chunk_sha = self._create_payload(dirs.chunks, payload_name, stream, length)
                except _StreamValidationError:
                    rejected = {**pending, "phase": "rejected"}
                    head = self._set_head(dirs, head, pending=rejected)
                    self._set_head(dirs, head, pending=None)
                    raise
                except FileExistsError as exc:
                    self._mark_recovery(dirs, head, "unsafe_entry")
                    raise AssetRecoveryRequiredError("chunk candidate has an unexpected winner") from exc
                verified = {**pending, "phase": "verified", "sha256": chunk_sha}
                head = self._set_head(dirs, head, pending=verified)
                record = {"schema": 1, "kind": "range", "owner_token": owner, "upload_id": upload_id, "operation_id": operation_id, "candidate_id": candidate_id, "offset": offset, "length": length, "sha256": chunk_sha}
                self._publish_immutable(dirs.ranges, target, record, candidate_id=operation_id)
                self._set_head(dirs, head, committed_offset=offset + length, pending=None)
                return offset + length
            finally:
                dirs.close()

    def _resume_pending(self, dirs: _UploadDirs, head: dict[str, Any]) -> dict[str, Any]:
        pending = head["pending"]
        if pending is None:
            return head
        if pending["kind"] == "range":
            if pending["offset"] != head["committed_offset"]:
                raise AssetRecoveryRequiredError("pending range does not match the committed frontier")
            if pending["phase"] == "rejected":
                return self._set_head(dirs, head, pending=None)
            if pending["phase"] == "prepared":
                raise AssetRecoveryRequiredError("range write outcome is unknown")
            payload = f"{pending['candidate_id']}.bin"
            self._verify_blob(dirs.chunks, payload, pending["sha256"], pending["length"]).close()
            target = self._range_target(pending["offset"])
            record = {"schema": 1, "kind": "range", "owner_token": head["owner_token"], "upload_id": head["upload_id"], "operation_id": pending["operation_id"], "candidate_id": pending["candidate_id"], "offset": pending["offset"], "length": pending["length"], "sha256": pending["sha256"]}
            self._publish_immutable(dirs.ranges, target, record, candidate_id=pending["operation_id"])
            return self._set_head(dirs, head, committed_offset=pending["offset"] + pending["length"], pending=None)
        if pending["phase"] == "prepared":
            raise AssetRecoveryRequiredError("publication outcome is unknown")
        return self._complete_publication(dirs, head)

    def _publish_blob(self, source_fd: int, source: str, target_fd: int, target: str, sha256: str, size: int) -> None:
        source_info = os.stat(source, dir_fd=source_fd, follow_symlinks=False)
        if self._entry_exists(target_fd, target):
            target_info = os.stat(target, dir_fd=target_fd, follow_symlinks=False)
            if _identity(target_info) != _identity(source_info):
                raise AssetRecoveryRequiredError("published payload has an unexpected winner")
            self._verify_blob(target_fd, target, sha256, size).close()
            return
        self._measure(_RECORD_BUDGET)
        os.link(source, target, src_dir_fd=source_fd, dst_dir_fd=target_fd, follow_symlinks=False)
        self._verify_blob(target_fd, target, sha256, size).close()
        os.fsync(target_fd)

    def _complete_publication(self, dirs: _UploadDirs, head: dict[str, Any]) -> dict[str, Any]:
        pending = head["pending"]
        assembly_name = f"{pending['candidate_id']}.bin"
        output_name = f"{head['asset_handle']}.blob"
        try:
            self._verify_blob(dirs.assembly, assembly_name, head["sha256"], head["size_bytes"]).close()
            self._publish_blob(dirs.assembly, assembly_name, self._published_fd, output_name, head["sha256"], head["size_bytes"])
            asset_record = {"schema": 1, "kind": "asset", "owner_token": head["owner_token"], "upload_id": head["upload_id"], "asset_handle": head["asset_handle"], "sha256": head["sha256"], "size_bytes": head["size_bytes"], "operation_id": pending["operation_id"]}
            self._publish_immutable(self._assets_fd, f"{head['asset_handle']}.json", asset_record, candidate_id=pending["operation_id"])
        except (OSError, AssetRecoveryRequiredError) as exc:
            self._mark_recovery(dirs, head, "unsafe_entry")
            raise AssetRecoveryRequiredError("publication evidence conflicts") from exc
        return self._set_head(dirs, head, state="published", pending=None, recovery_reason=None)

    def finish_upload(self, owner_scope: str, upload_id: str) -> dict[str, Any]:
        owner = _owner_token(owner_scope)
        upload_id = _upload_id(upload_id)
        with self._exclusive():
            dirs = self._open_owned_upload(owner, upload_id)
            try:
                head = self._resume_pending(dirs, self._head(dirs))
                if head["state"] == "recovery_required":
                    raise AssetRecoveryRequiredError("render upload requires recovery")
                if head["state"] == "published":
                    try:
                        asset = self._owned_asset(owner, head["asset_handle"])
                        self._verify_blob(self._published_fd, f"{head['asset_handle']}.blob", head["sha256"], head["size_bytes"]).close()
                    except (AssetNotFoundError, AssetRecoveryRequiredError) as exc:
                        self._mark_recovery(dirs, head, "integrity")
                        raise AssetRecoveryRequiredError("published asset evidence is inconsistent") from exc
                    return self._asset_descriptor(asset)
                if head["committed_offset"] != head["size_bytes"]:
                    raise RenderAssetError("UPLOAD_INCOMPLETE", "render upload is incomplete")
                self._validate_range_records(dirs, head)
                self._measure(head["size_bytes"] + 10 * _RECORD_BUDGET)
                operation_id, candidate_id = uuid.uuid4().hex, uuid.uuid4().hex
                pending = {"kind": "publication", "operation_id": operation_id, "candidate_id": candidate_id, "phase": "prepared"}
                head = self._set_head(dirs, head, pending=pending)
                assembly_name = f"{candidate_id}.bin"
                descriptor = os.open(assembly_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dirs.assembly)
                total_hash = hashlib.sha256()
                total_size = 0
                try:
                    offset = 0
                    while offset < head["size_bytes"]:
                        record = self._range_record(dirs, offset)
                        with self._verify_blob(dirs.chunks, f"{record['candidate_id']}.bin", record["sha256"], record["length"]) as source:
                            while piece := source.read(_BUFFER_BYTES):
                                total_hash.update(piece)
                                total_size += len(piece)
                                _write_all(descriptor, piece)
                        offset += record["length"]
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.fsync(dirs.assembly)
                if total_size != head["size_bytes"] or total_hash.hexdigest() != head["sha256"]:
                    self._mark_recovery(dirs, head, "integrity")
                    raise RenderAssetError("CHECKSUM_MISMATCH", "render asset checksum mismatch")
                head = self._set_head(dirs, head, pending={**pending, "phase": "verified"})
                head = self._complete_publication(dirs, head)
                asset = self._owned_asset(owner, head["asset_handle"])
                return self._asset_descriptor(asset)
            finally:
                dirs.close()

    def _owned_asset_record(self, owner: str, handle: str) -> dict[str, Any]:
        try:
            name = f"{handle}.json"
            if not self._entry_exists(self._assets_fd, name):
                raise AssetNotFoundError("render asset not found")
            asset, _, _ = self._read_record(self._assets_fd, name, "asset")
        except AssetNotFoundError:
            raise
        except (OSError, AssetRecoveryRequiredError) as exc:
            raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe") from exc
        if asset["owner_token"] != owner:
            raise AssetNotFoundError("render asset not found")
        return asset

    def _owned_asset(self, owner: str, handle: str) -> dict[str, Any]:
        asset = self._owned_asset_record(owner, handle)
        try:
            dirs = self._open_owned_upload(owner, asset["upload_id"])
        except AssetNotFoundError as exc:
            raise AssetRecoveryRequiredError("asset upload evidence is inconsistent") from exc
        try:
            head = self._resume_pending(dirs, self._head(dirs))
            if head["state"] == "recovery_required":
                raise AssetRecoveryRequiredError("render asset requires recovery")
            if head["state"] != "published" or head["asset_handle"] != handle:
                raise AssetNotFoundError("render asset not found")
            fields = ("owner_token", "upload_id", "asset_handle", "sha256", "size_bytes")
            if any(asset[field] != head[field] for field in fields):
                raise AssetRecoveryRequiredError("asset lookup does not match its published head")
        finally:
            dirs.close()
        return asset

    def _owned_asset_metadata(self, owner: str, handle: str) -> dict[str, Any]:
        asset = self._owned_asset_record(owner, handle)
        if asset["asset_handle"] != handle:
            raise AssetNotFoundError("render asset not found")
        upload_fd = None
        try:
            upload_fd = os.open(asset["upload_id"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._uploads_fd)
            opened = os.fstat(upload_fd)
            visible = os.stat(asset["upload_id"], dir_fd=self._uploads_fd, follow_symlinks=False)
            if not stat.S_ISDIR(opened.st_mode) or _identity(opened) != _identity(visible):
                raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe")
            try:
                head, _, _ = self._read_record(upload_fd, "head.json", "upload_head")
            except AssetRecoveryRequiredError as exc:
                raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe") from exc
            if head["upload_id"] != asset["upload_id"]:
                raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe")
            if head["owner_token"] != owner:
                raise AssetNotFoundError("render asset not found")
            self._validate_head_init(head)
            fields = ("owner_token", "upload_id", "asset_handle", "sha256", "size_bytes")
            if any(asset[field] != head[field] for field in fields):
                raise AssetRecoveryRequiredError("asset lookup does not match its upload head")
            pending = head["pending"]
            proven_pending = (
                head["state"] == "incomplete"
                and pending is not None
                and pending["kind"] == "publication"
                and pending["phase"] == "verified"
                and pending["operation_id"] == asset["operation_id"]
            )
            if head["state"] not in {"published", "recovery_required"} and not proven_pending:
                raise AssetRecoveryRequiredError("asset publication metadata is incomplete")
            return asset
        except FileNotFoundError as exc:
            raise AssetRecoveryRequiredError("asset upload evidence is unavailable") from exc
        except OSError as exc:
            raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe") from exc
        finally:
            if upload_fd is not None:
                os.close(upload_fd)

    def describe(self, owner_scope: str, asset_handle: str) -> dict[str, Any]:
        owner = _owner_token(owner_scope)
        asset_handle = _asset_id(asset_handle)
        with self._exclusive():
            asset = self._owned_asset_metadata(owner, asset_handle)
            return self._asset_descriptor(asset)

    def open_reader(self, owner_scope: str, asset_handle: str) -> _ReaderLease:
        if self._closed:
            raise RenderAssetError("STORE_CLOSED", "render asset store is closed")
        return _ReaderLease(self, _owner_token(owner_scope), _asset_id(asset_handle))

    def _protection(self, dirs: _UploadDirs, owner: str, handle: str, kind: str, reference: str, state: str) -> dict[str, Any]:
        key = _protection_key(owner, handle, kind, reference)
        target = f"{key}.json"
        if self._entry_exists(dirs.protections, target):
            old, _, _ = self._read_record(dirs.protections, target, "protection")
            if (old["owner_token"], old["asset_handle"], old["protection_kind"], old["reference"]) != (owner, handle, kind, reference):
                raise AssetRecoveryRequiredError("protection key identity changed")
            if old["state"] == state:
                return old
            generation = old["generation"] + 1
        else:
            generation = 0
        record = {"schema": 1, "kind": "protection", "owner_token": owner, "upload_id": dirs.upload_id, "asset_handle": handle, "protection_kind": kind, "reference": reference, "generation": generation, "state": state}
        self._measure(4 * _RECORD_BUDGET)
        self._change_record(dirs, dirs.protections, target, "protection", record)
        return record

    def _enter_reader(self, reader: _ReaderLease) -> None:
        with self._exclusive():
            asset = self._owned_asset(reader._owner_token, reader._asset_handle)
            try:
                handle = self._verify_blob(self._published_fd, f"{reader._asset_handle}.blob", asset["sha256"], asset["size_bytes"])
            except AssetRecoveryRequiredError:
                dirs = self._open_owned_upload(reader._owner_token, asset["upload_id"])
                try:
                    self._mark_recovery(dirs, self._head(dirs), "integrity")
                finally:
                    dirs.close()
                raise
            lease_id = uuid.uuid4().hex
            dirs = None
            try:
                dirs = self._open_owned_upload(reader._owner_token, asset["upload_id"])
                self._protection(dirs, reader._owner_token, reader._asset_handle, "reader", lease_id, "active")
            except BaseException:
                handle.close()
                raise
            finally:
                if dirs is not None:
                    dirs.close()
            reader._file, reader.sha256, reader.size_bytes, reader._lease_id = handle, asset["sha256"], asset["size_bytes"], lease_id
            self._remember_reader(reader)

    def _remember_reader(self, reader: _ReaderLease) -> None:
        with self._lock:
            self._open_readers.add(reader)

    def _forget_reader(self, reader: _ReaderLease) -> None:
        with self._lock:
            self._open_readers.discard(reader)

    def _set_reader_state(self, owner: str, handle: str, lease_id: str, state: str) -> None:
        asset = self._owned_asset(owner, handle)
        dirs = self._open_owned_upload(owner, asset["upload_id"])
        try:
            self._protection(dirs, owner, handle, "reader", lease_id, state)
        finally:
            dirs.close()

    def _release_reader(self, owner: str, handle: str, lease_id: str) -> None:
        with self._exclusive():
            self._set_reader_state(owner, handle, lease_id, "released")

    def _unknown_reader(self, owner: str, handle: str, lease_id: str) -> None:
        with self._exclusive():
            self._set_reader_state(owner, handle, lease_id, "unknown")

    def acquire_reference(self, owner_scope: str, asset_handle: str, reference_id: str) -> None:
        owner = _owner_token(owner_scope)
        asset_handle = _asset_id(asset_handle)
        reference = _validated_reference(reference_id)
        with self._exclusive():
            asset = self._owned_asset(owner, asset_handle)
            dirs = self._open_owned_upload(owner, asset["upload_id"])
            try:
                self._protection(dirs, owner, asset_handle, "reference", reference, "active")
            finally:
                dirs.close()

    def release_reference(self, owner_scope: str, asset_handle: str, reference_id: str) -> None:
        owner = _owner_token(owner_scope)
        asset_handle = _asset_id(asset_handle)
        reference = _validated_reference(reference_id)
        with self._exclusive():
            asset = self._owned_asset(owner, asset_handle)
            dirs = self._open_owned_upload(owner, asset["upload_id"])
            try:
                self._protection(dirs, owner, asset_handle, "reference", reference, "released")
            finally:
                dirs.close()

    def release_reference_everywhere(self, owner_scope: str, reference_id: str) -> int:
        """Release one job-scoped reference on every asset it still protects.

        A render job owns one reference for all of its inputs, so a job that has
        durably settled can be released from its dispatch key alone, without the
        request's asset handles. Releases are idempotent, and a reference that is
        already released or belongs to another job is left untouched. Evidence that
        cannot be read is skipped, never guessed at: the caller retries later.
        """
        owner = _owner_token(owner_scope)
        reference = _validated_reference(reference_id)
        released = 0
        with self._exclusive():
            with _scan_entries(self._uploads_fd) as uploads:
                names = sorted(entry.name for entry in uploads)
            for name in names:
                if _ID.fullmatch(name) is None:
                    raise AssetRecoveryRequiredError("unexpected upload directory")
                try:
                    dirs = self._open_owned_upload(owner, name)
                except (AssetNotFoundError, RenderAssetError):
                    continue
                try:
                    self._recover_changes(dirs)
                    with _scan_entries(dirs.protections) as protections:
                        leaves = sorted(entry.name for entry in protections)
                    for leaf in leaves:
                        if re.fullmatch(r"[0-9a-f]{64}\.json", leaf) is None:
                            raise AssetRecoveryRequiredError(
                                "upload has unknown protection evidence")
                        record, _, _ = self._read_record(
                            dirs.protections, leaf, "protection")
                        if (record["owner_token"] != owner
                                or record["protection_kind"] != "reference"
                                or record["reference"] != reference
                                or record["state"] not in {"active", "unknown"}):
                            continue
                        self._protection(
                            dirs, owner, record["asset_handle"], "reference",
                            reference, "released")
                        released += 1
                except RenderAssetError:
                    continue
                finally:
                    dirs.close()
        return released

    def active_protection(self, owner_scope: str) -> list[dict[str, Any]]:
        owner = _owner_token(owner_scope)
        grouped: dict[str, dict[str, Any]] = {}
        with self._exclusive():
            with _scan_entries(self._uploads_fd) as uploads:
                for entry in uploads:
                    if not _ID.fullmatch(entry.name):
                        raise AssetRecoveryRequiredError("unexpected upload directory")
                    try:
                        dirs = self._open_owned_upload(owner, entry.name)
                    except AssetNotFoundError:
                        continue
                    try:
                        self._recover_changes(dirs)
                        head = self._validated_head(dirs)
                        with _scan_entries(dirs.protections) as protections:
                            for leaf in protections:
                                if re.fullmatch(r"[0-9a-f]{64}\.json", leaf.name) is None:
                                    raise AssetRecoveryRequiredError("upload has unknown protection evidence")
                                record, _, _ = self._read_record(dirs.protections, leaf.name, "protection")
                                expected = _protection_key(record["owner_token"], record["asset_handle"], record["protection_kind"], record["reference"])
                                if leaf.name != f"{expected}.json":
                                    raise AssetRecoveryRequiredError("protection lookup identity changed")
                                fields = ("owner_token", "upload_id", "asset_handle")
                                if any(record[field] != head[field] for field in fields):
                                    raise AssetRecoveryRequiredError("protection record does not match its upload")
                                if record["owner_token"] == owner and record["state"] in {"active", "unknown"}:
                                    row = grouped.setdefault(record["asset_handle"], {
                                        "asset_handle": record["asset_handle"],
                                        "reader_leases": 0,
                                        "references": [],
                                    })
                                    if record["protection_kind"] == "reader":
                                        row["reader_leases"] += 1
                                    else:
                                        row["references"].append(record["reference"])
                    finally:
                        dirs.close()
        for row in grouped.values():
            row["references"] = sorted(set(row["references"]))
        return [grouped[handle] for handle in sorted(grouped)]

    @staticmethod
    def _newest_mtime(descriptor: int, depth: int = 2) -> float:
        """The newest touch under one upload, so a live range upload is not aged out."""
        newest = os.fstat(descriptor).st_mtime
        with _scan_entries(descriptor) as entries:
            names = [entry.name for entry in entries]
        for name in names:
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            newest = max(newest, info.st_mtime)
            if depth > 1 and stat.S_ISDIR(info.st_mode):
                child = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor)
                try:
                    newest = max(
                        newest,
                        RenderAssetStore._newest_mtime(child, depth - 1))
                finally:
                    os.close(child)
        return newest

    def _retire_day(self, now: float) -> int:
        """Open `_trash/retired/<UTC date>` for one sweep, creating only real folders."""
        try:
            os.mkdir(_RETIRE_BUCKET, 0o700, dir_fd=self._trash_fd)
        except FileExistsError:
            pass
        bucket = os.open(
            _RETIRE_BUCKET, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=self._trash_fd)
        try:
            opened = os.fstat(bucket)
            visible = os.stat(
                _RETIRE_BUCKET, dir_fd=self._trash_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(opened.st_mode)
                    or _identity(opened) != _identity(visible)):
                raise RenderAssetError("STORE_UNSAFE", "render asset store is unsafe")
            day = time.strftime("%Y-%m-%d", time.gmtime(now))
            try:
                os.mkdir(day, 0o700, dir_fd=bucket)
            except FileExistsError:
                pass
            day_fd = os.open(
                day, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=bucket)
            try:
                opened = os.fstat(day_fd)
                visible = os.stat(day, dir_fd=bucket, follow_symlinks=False)
                if (not stat.S_ISDIR(opened.st_mode)
                        or _identity(opened) != _identity(visible)):
                    raise RenderAssetError(
                        "STORE_UNSAFE", "render asset store is unsafe")
                os.fsync(bucket)
                return day_fd
            except BaseException:
                os.close(day_fd)
                raise
        finally:
            os.close(bucket)

    @staticmethod
    def _ensure_trash_root(path: Path) -> None:
        """Create the trash root only when its parent already exists and is a real dir."""
        parent = Path(path).parent
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OSError("trash parent is not a real directory")
        try:
            Path(path).mkdir(mode=0o700)
        except FileExistsError:
            pass

    def _retire_day_at(self, destination: Path, now: float) -> int:
        """Open a dated folder in the Hub's one Trash Folder, refusing link swaps."""
        base = Path(destination)
        try:
            self._ensure_trash_root(base)
        except OSError as exc:
            raise RenderAssetError(
                "STORE_UNSAFE", "render asset trash is unavailable") from exc
        try:
            info = base.lstat()
        except OSError as exc:
            raise RenderAssetError(
                "STORE_UNSAFE", "render asset trash is unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RenderAssetError("STORE_UNSAFE", "render asset trash is unsafe")
        day = base / time.strftime("%Y-%m-%d", time.gmtime(now))
        try:
            day.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise RenderAssetError(
                "STORE_UNSAFE", "render asset trash is unavailable") from exc
        try:
            day_fd = os.open(
                day, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise RenderAssetError("STORE_UNSAFE", "render asset trash is unsafe") from exc
        try:
            # The open is identity-checked; the later rename is path-based, so this is
            # not a claim of symlink-swap protection, only a fail-closed sanity check.
            opened = os.fstat(day_fd)
            visible = day.lstat()
            if (not stat.S_ISDIR(opened.st_mode)
                    or _identity(opened) != _identity(visible)):
                raise RenderAssetError(
                    "STORE_UNSAFE", "render asset trash is unsafe")
            return day_fd
        except BaseException:
            os.close(day_fd)
            raise

    def _retire_upload(
        self, name: str, now: float,
        unpublished_after: float, published_after: float,
        open_day: Callable[[], int],
    ) -> str | None:
        """One upload, or None when any evidence says it is still owned or unresolved."""
        try:
            upload_fd = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=self._uploads_fd)
        except FileNotFoundError:
            return None
        try:
            head, _, _ = self._read_record(upload_fd, "head.json", "upload_head")
            if (head["upload_id"] != name
                    or head["state"] not in {"incomplete", "published"}
                    or head["pending"] is not None):
                return None
            try:
                protections = os.open(
                    "protections", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=upload_fd)
            except OSError:
                return None
            try:
                with _scan_entries(protections) as entries:
                    leaves = sorted(entry.name for entry in entries)
                for leaf in leaves:
                    if re.fullmatch(r"[0-9a-f]{64}\.json", leaf) is None:
                        return None
                    record, _, _ = self._read_record(
                        protections, leaf, "protection")
                    if (record["owner_token"], record["upload_id"],
                            record["asset_handle"]) != (
                            head["owner_token"], head["upload_id"],
                            head["asset_handle"]):
                        return None
                    expected = _protection_key(
                        record["owner_token"], record["asset_handle"],
                        record["protection_kind"], record["reference"])
                    if leaf != f"{expected}.json":
                        return None
                    if record["state"] in {"active", "unknown"}:
                        return None
            finally:
                os.close(protections)
            try:
                changes = os.open(
                    "changes", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=upload_fd)
            except OSError:
                return None
            try:
                with _scan_entries(changes) as entries:
                    for entry in entries:
                        if re.fullmatch(
                                r"candidate-[0-9a-f]{32}\.json",
                                entry.name) is None:
                            return None
            finally:
                os.close(changes)
            grace = published_after if head["state"] == "published" else unpublished_after
            if now - self._newest_mtime(upload_fd) < grace:
                return None
            day_fd = open_day()
            target = f"{name}-{uuid.uuid4().hex[:8]}"
            os.mkdir(target, 0o700, dir_fd=day_fd)
            os.fsync(day_fd)
            target_fd = os.open(
                target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=day_fd)
            try:
                for source_fd, source, destination in (
                    (self._index_fd,
                     f"{head['owner_token']}-{head['sha256']}.json", "init.json"),
                    (self._assets_fd, f"{head['asset_handle']}.json", "asset.json"),
                    (self._published_fd,
                     f"{head['asset_handle']}.blob", "published.blob"),
                ):
                    if self._entry_exists(source_fd, source):
                        os.rename(
                            source, destination,
                            src_dir_fd=source_fd, dst_dir_fd=target_fd)
                os.rename(
                    name, "upload",
                    src_dir_fd=self._uploads_fd, dst_dir_fd=target_fd)
                for descriptor in (
                        target_fd, day_fd, self._uploads_fd,
                        self._index_fd, self._assets_fd, self._published_fd):
                    os.fsync(descriptor)
            finally:
                os.close(target_fd)
            return head["state"]
        finally:
            os.close(upload_fd)

    def retire_stale(
        self, *, now: float | None = None,
        unpublished_after: float = _RETIRE_AFTER_S,
        published_after: float = _RETIRE_AFTER_S,
        destination: Path | None = None,
    ) -> dict[str, int]:
        """Move aged, unreferenced uploads and their published bytes into `_trash`.

        This is retirement, not deletion: every file is renamed into a dated folder,
        by default the store's own `_trash/retired/<UTC date>/`, or `destination` —
        the Hub's one Trash Folder — when the caller supplies it. Anything still
        referenced, being read, unresolved (`recovery_required`, pending publication),
        or younger than its grace is retained untouched.
        """
        now = time.time() if now is None else now
        for label, value in (
                ("now", now), ("unpublished_after", unpublished_after),
                ("published_after", published_after)):
            if (isinstance(value, bool) or type(value) not in (int, float)
                    or value < 0 or not value < float("inf")):
                raise AssetValidationError(
                    f"{label} must be a finite non-negative number")
        retired = published = retained = 0
        with self._exclusive():
            day: dict[str, int | None] = {"fd": None}

            def open_day() -> int:
                if day["fd"] is None:
                    day["fd"] = (
                        self._retire_day_at(destination, now)
                        if destination is not None else self._retire_day(now))
                return int(day["fd"])

            try:
                with _scan_entries(self._uploads_fd) as uploads:
                    names = sorted(entry.name for entry in uploads)
                for name in names:
                    if _ID.fullmatch(name) is None:
                        raise AssetRecoveryRequiredError("unexpected upload directory")
                    try:
                        state = self._retire_upload(
                            name, now, unpublished_after, published_after, open_day)
                    except (RenderAssetError, OSError):
                        state = None
                    if state is None:
                        retained += 1
                        continue
                    retired += 1
                    if state == "published":
                        published += 1
            finally:
                if day["fd"] is not None:
                    os.close(int(day["fd"]))
        return {
            "retired": retired, "published": published, "retained": retained,
        }
