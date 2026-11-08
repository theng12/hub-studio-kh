"""Durable per-attempt gate store and workspace lifecycle primitive.

Minimal standard-library storage for developer-gate attempts under an exclusive owned root.
Enforces safe path boundaries, atomic fsynced journals, idempotent identity reconciliation,
aggregate workspace and spool capacity accounting, exact-match ACK before result byte removal,
and guarded disposable scratch cleanup with confirmed process release.
"""

from __future__ import annotations

import copy
import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ALLOWED_STATES = frozenset({
    "waiting_source", "waiting_idle", "preparing_toolchains", "running",
    "collecting", "passed", "failed", "cancelled", "interrupted", "not_run", "recovery_hold",
})
TERMINAL_STATES = frozenset({"passed", "failed", "cancelled", "interrupted", "not_run"})

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "waiting_source": {"waiting_source", "waiting_idle", "cancelled", "interrupted", "recovery_hold", "not_run"},
    "waiting_idle": {"waiting_idle", "preparing_toolchains", "running", "cancelled", "interrupted", "recovery_hold"},
    "preparing_toolchains": {"preparing_toolchains", "waiting_idle", "running", "failed", "cancelled", "interrupted", "recovery_hold"},
    "running": {"running", "collecting", "passed", "failed", "cancelled", "interrupted", "recovery_hold"},
    "collecting": {"collecting", "passed", "failed", "cancelled", "interrupted", "recovery_hold"},
    "passed": {"passed"},
    "failed": {"failed"},
    "cancelled": {"cancelled"},
    "interrupted": {"interrupted"},
    "not_run": {"not_run"},
    "recovery_hold": {"recovery_hold", "interrupted", "cancelled", "failed"},
}


class GateStoreError(Exception): status_code: int = 500
class GateStoreConflictError(GateStoreError): status_code: int = 409
class GateStoreNotFoundError(GateStoreError): status_code: int = 404
class GateStoreCapacityError(GateStoreError): status_code: int = 409
class GateStorePathError(GateStoreError): status_code: int = 400
class GateStoreStateError(GateStoreError): status_code: int = 400


def validate_safe_id(val: str, field: str = "id") -> str:
    if not isinstance(val, str) or not _SAFE_ID_RE.match(val):
        raise GateStorePathError(f"Invalid safe identifier for {field}: {val!r}")
    return val


def canonical_json_bytes(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def compute_request_sha256(canonical_bytes: bytes) -> str:
    return hashlib.sha256(canonical_bytes).hexdigest()


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fsync_dir(path: Path) -> None:
    dfd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp.{uuid.uuid4().hex}"
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with open(fd, "wb") as f:
            f.write(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True).encode("utf-8"))
            f.flush()
            os.fsync(fd)
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


class DeveloperGateStore:
    def __init__(
        self,
        root_dir: str | Path,
        max_workspace_bytes: int,
        max_result_spool_bytes: int,
        min_workspace_bytes: int = 1,
        min_result_spool_bytes: int = 1,
        max_attempt_workspace_bytes: int | None = None,
        max_attempt_spool_bytes: int | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._closed: bool = False
        self._lock_fd: int | None = None
        self._attempts: dict[str, dict[str, Any]] = {}
        self._key_to_attempt: dict[str, str] = {}
        self._active_workspace_bytes: int = 0
        self._active_spool_bytes: int = 0

        if isinstance(max_workspace_bytes, bool) or not isinstance(max_workspace_bytes, int) or max_workspace_bytes <= 0:
            raise GateStoreCapacityError("max_workspace_bytes must be a strict positive integer")
        if isinstance(max_result_spool_bytes, bool) or not isinstance(max_result_spool_bytes, int) or max_result_spool_bytes <= 0:
            raise GateStoreCapacityError("max_result_spool_bytes must be a strict positive integer")
        if isinstance(min_workspace_bytes, bool) or not isinstance(min_workspace_bytes, int) or min_workspace_bytes <= 0:
            raise GateStoreCapacityError("min_workspace_bytes must be a strict positive integer")
        if isinstance(min_result_spool_bytes, bool) or not isinstance(min_result_spool_bytes, int) or min_result_spool_bytes <= 0:
            raise GateStoreCapacityError("min_result_spool_bytes must be a strict positive integer")
        if max_attempt_workspace_bytes is not None:
            if isinstance(max_attempt_workspace_bytes, bool) or not isinstance(max_attempt_workspace_bytes, int) or max_attempt_workspace_bytes < min_workspace_bytes:
                raise GateStoreCapacityError("max_attempt_workspace_bytes must be >= min_workspace_bytes")
        if max_attempt_spool_bytes is not None:
            if isinstance(max_attempt_spool_bytes, bool) or not isinstance(max_attempt_spool_bytes, int) or max_attempt_spool_bytes < min_result_spool_bytes:
                raise GateStoreCapacityError("max_attempt_spool_bytes must be >= min_result_spool_bytes")

        raw_root = Path(root_dir)
        if os.path.islink(str(raw_root)):
            raise GateStorePathError(f"Root dir cannot be a symlink: {raw_root}")
        raw_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(raw_root, 0o700)
        except OSError:
            pass
        self.root_dir = raw_root.resolve()

        self.min_workspace_bytes = min_workspace_bytes
        self.min_result_spool_bytes = min_result_spool_bytes
        self.max_attempt_workspace_bytes = max_attempt_workspace_bytes
        self.max_attempt_spool_bytes = max_attempt_spool_bytes

        self._lock_file = self.root_dir / ".store_owner.lock"
        try:
            self._lock_fd = os.open(str(self._lock_file), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as e:
            raise GateStoreConflictError(f"Store root {self.root_dir} is already locked by another process: {e}")

        self.max_workspace_bytes = max_workspace_bytes
        self.max_result_spool_bytes = max_result_spool_bytes
        self.attempts_dir = self.root_dir / "attempts"
        self.workspaces_dir = self.root_dir / "workspaces"
        self.spool_dir = self.root_dir / "spool"

        self._init_storage()

    def _ensure_open(self) -> None:
        if self._closed:
            raise GateStoreError("DeveloperGateStore has been closed")

    def close(self) -> None:
        if hasattr(self, "_lock"):
            with self._lock:
                self._closed = True
                if getattr(self, "_lock_fd", None) is not None:
                    try:
                        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                        os.close(self._lock_fd)
                    except OSError:
                        pass
                    self._lock_fd = None

    def __del__(self) -> None:
        self.close()

    def _safe_mkdir_nosymlink(self, path: Path) -> None:
        if path.exists():
            if os.path.islink(str(path)) or not path.is_dir():
                raise GateStorePathError(f"Directory cannot be symlink or file: {path}")
            if path.resolve() != self.root_dir / path.name:
                raise GateStorePathError(f"Path boundary violation: {path}")
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass
        else:
            path.mkdir(mode=0o700, parents=True, exist_ok=False)

    def _derive_active_workspace_bytes(self) -> int:
        total = 0
        for rec in self._attempts.values():
            if not rec.get("workspace_removed"):
                total += int(rec.get("requested_workspace_bytes", 0))
        return total

    def _derive_active_spool_bytes(self) -> int:
        total = 0
        for att_id, rec in self._attempts.items():
            res_reserved = int(rec.get("reserved_spool_bytes", 0))
            is_acked = bool(rec.get("result_acknowledged"))
            spool_sub = self.spool_dir / att_id
            tar = spool_sub / "result.tar"
            if tar.is_file() and not os.path.islink(str(tar)):
                try:
                    total += tar.stat().st_size
                except OSError as e:
                    raise GateStoreError(f"Failed to account spool result {att_id}: {e}")
            elif not is_acked and not rec.get("result"):
                total += res_reserved
            elif not is_acked and rec.get("result"):
                total += int(rec["result"].get("size_bytes", 0))

            if spool_sub.is_dir() and not os.path.islink(str(spool_sub)):
                try:
                    for item in spool_sub.iterdir():
                        if item.name.startswith("result.tar.tmp.") and item.is_file() and not os.path.islink(str(item)):
                            total += item.stat().st_size
                except OSError as e:
                    raise GateStoreError(f"Failed to account spool temporaries {att_id}: {e}")
        return total

    def _init_storage(self) -> None:
        self._safe_mkdir_nosymlink(self.attempts_dir)
        self._safe_mkdir_nosymlink(self.workspaces_dir)
        self._safe_mkdir_nosymlink(self.spool_dir)

        # 1. Load attempt journals with strict validation
        for f in self.attempts_dir.iterdir():
            if os.path.islink(str(f)):
                raise GateStorePathError(f"Attempt file cannot be symlink: {f.name}")
            if f.is_file() and (".tmp." in f.name or f.name.endswith(".tmp")):
                continue
            if not f.is_file() or not f.name.endswith(".json") or f.name.startswith("."):
                raise GateStoreError(f"Untracked file in attempts directory: {f.name}")
            try:
                fd = os.open(str(f), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                try:
                    with open(fd, "r", encoding="utf-8") as jf:
                        rec = json.load(jf)
                finally:
                    pass
            except Exception as e:
                raise GateStoreError(f"Corrupt attempt journal {f.name}: {e}")

            acc = rec.get("acceptance")
            if not isinstance(acc, dict):
                raise GateStoreError(f"Malformed journal acceptance object in {f.name}")

            att_id = acc.get("attempt_id")
            key = acc.get("idempotency_key")
            ctrl_id = acc.get("controller_id")
            node_id = acc.get("node_id")
            req_sha = acc.get("request_sha256")
            if not att_id or not key or not ctrl_id or not node_id or not req_sha:
                raise GateStoreError(f"Missing required identity fields in acceptance {f.name}")

            validate_safe_id(att_id, "attempt_id")
            validate_safe_id(key, "idempotency_key")
            validate_safe_id(ctrl_id, "controller_id")
            validate_safe_id(node_id, "node_id")

            if f.name != f"{att_id}.json":
                raise GateStoreError(f"Journal filename does not match attempt_id: {f.name} vs {att_id}.json")

            if att_id in self._attempts:
                raise GateStoreError(f"Duplicate attempt_id in journals: {att_id}")
            if key in self._key_to_attempt:
                raise GateStoreError(f"Duplicate idempotency_key in journals: {key}")

            req_ws = rec.get("requested_workspace_bytes")
            if isinstance(req_ws, bool) or not isinstance(req_ws, int) or req_ws <= 0:
                raise GateStoreError(f"Invalid requested_workspace_bytes in journal {f.name}: {req_ws}")
            res_sp = rec.get("reserved_spool_bytes")
            if isinstance(res_sp, bool) or not isinstance(res_sp, int) or res_sp <= 0:
                raise GateStoreError(f"Invalid reserved_spool_bytes in journal {f.name}: {res_sp}")

            self._attempts[att_id] = rec
            self._key_to_attempt[key] = att_id

        # 2. Account workspaces - Fail closed on ANY untracked workspace or file
        for d in self.workspaces_dir.iterdir():
            if os.path.islink(str(d)):
                raise GateStorePathError(f"Symlink in workspace directory: {d.name}")
            if not d.is_dir() or d.name.startswith("."):
                raise GateStoreError(f"Untracked file in workspaces directory: {d.name}")
            if d.name not in self._attempts:
                raise GateStoreError(f"Untracked workspace directory detected: {d.name}")

        # 3. Account spool entries strictly
        for d in self.spool_dir.iterdir():
            if os.path.islink(str(d)):
                raise GateStorePathError(f"Symlink in spool directory: {d.name}")
            if not d.is_dir() or d.name.startswith("."):
                raise GateStoreError(f"Untracked non-directory in spool directory: {d.name}")
            if d.name not in self._attempts:
                raise GateStoreError(f"Untracked attempt directory in spool directory: {d.name}")

            for item in d.iterdir():
                if os.path.islink(str(item)):
                    raise GateStorePathError(f"Symlink in spool item: {item.name}")
                if item.name == "result.tar":
                    if not item.is_file() or item.name.startswith("."):
                        raise GateStoreError(f"Spool result.tar is not a regular file in {d.name}")
                    try:
                        _ = item.stat().st_size
                    except OSError as e:
                        raise GateStoreError(f"Failed to stat spool file {item.name} in {d.name}: {e}")
                elif item.name.startswith("result.tar.tmp."):
                    if not item.is_file():
                        raise GateStoreError(f"Spool tmp is not a regular file: {item.name}")
                    try:
                        _ = item.stat().st_size
                    except OSError as e:
                        raise GateStoreError(f"Failed to stat spool tmp file {item.name} in {d.name}: {e}")
                else:
                    raise GateStoreError(f"Untracked spool file detected: {item.name} in {d.name}")

        self._active_workspace_bytes = self._derive_active_workspace_bytes()
        self._active_spool_bytes = self._derive_active_spool_bytes()
        if self._active_workspace_bytes > self.max_workspace_bytes:
            raise GateStoreCapacityError("Active workspace bytes exceed capacity")
        if self._active_spool_bytes > self.max_result_spool_bytes:
            raise GateStoreCapacityError("Active spool bytes exceed capacity")

    def _assert_safe_disposable_path(self, target: Path, expected_parent: Path) -> None:
        if os.path.islink(str(target)):
            raise GateStorePathError(f"Symlinks strictly forbidden for workspace/spool: {target}")
        rp, rt = expected_parent.resolve(), target.resolve()
        try:
            rel = rt.relative_to(rp)
        except ValueError:
            raise GateStorePathError(f"Path traversal or boundary violation: {target} outside {expected_parent}")
        if not rel.parts or rel.parts[0] in (".", ".."):
            raise GateStorePathError(f"Target cannot be parent root: {target}")
        curr = target
        while curr != expected_parent and curr != curr.parent:
            if os.path.islink(str(curr)):
                raise GateStorePathError(f"Symlink in hierarchy: {curr}")
            curr = curr.parent

    def accept_attempt(
        self,
        controller_id: str,
        node_id: str,
        idempotency_key: str,
        canonical_request_bytes: bytes,
        request_sha256: str | None = None,
        requested_workspace_bytes: int | None = None,
        reserved_spool_bytes: int | None = None,
        attempt_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_open()
            validate_safe_id(controller_id, "controller_id")
            validate_safe_id(node_id, "node_id")
            validate_safe_id(idempotency_key, "idempotency_key")

            if requested_workspace_bytes is None or isinstance(requested_workspace_bytes, bool) or not isinstance(requested_workspace_bytes, int) or requested_workspace_bytes < self.min_workspace_bytes:
                raise GateStoreCapacityError(f"requested_workspace_bytes must be a strict positive integer >= {self.min_workspace_bytes}")
            if reserved_spool_bytes is None or isinstance(reserved_spool_bytes, bool) or not isinstance(reserved_spool_bytes, int) or reserved_spool_bytes < self.min_result_spool_bytes:
                raise GateStoreCapacityError(f"reserved_spool_bytes must be a strict positive integer >= {self.min_result_spool_bytes}")
            if self.max_attempt_workspace_bytes is not None and requested_workspace_bytes > self.max_attempt_workspace_bytes:
                raise GateStoreCapacityError(f"requested_workspace_bytes exceeds maximum allowed {self.max_attempt_workspace_bytes}")
            if self.max_attempt_spool_bytes is not None and reserved_spool_bytes > self.max_attempt_spool_bytes:
                raise GateStoreCapacityError(f"reserved_spool_bytes exceeds maximum allowed {self.max_attempt_spool_bytes}")

            if not isinstance(canonical_request_bytes, (bytes, bytearray)):
                raise TypeError("canonical_request_bytes must be bytes")
            if len(canonical_request_bytes) > 10 * 1024 * 1024:
                raise GateStoreCapacityError("canonical_request_bytes exceeds 10MB limit")

            computed_sha = compute_request_sha256(canonical_request_bytes)
            if request_sha256 is not None and request_sha256 != computed_sha:
                raise GateStoreConflictError(f"request_sha256 mismatch: {request_sha256} vs {computed_sha}")
            sha = computed_sha

            if idempotency_key in self._key_to_attempt:
                existing = self._attempts[self._key_to_attempt[idempotency_key]]
                ea = existing["acceptance"]
                if (
                    ea["controller_id"] == controller_id and ea["node_id"] == node_id
                    and ea["idempotency_key"] == idempotency_key
                    and ea["request_sha256"] == sha
                    and existing.get("canonical_request_hex") == canonical_request_bytes.hex()
                ):
                    return copy.deepcopy(existing)
                raise GateStoreConflictError(f"Idempotency conflict for key {idempotency_key!r}: request identity differs")

            if self._active_workspace_bytes + requested_workspace_bytes > self.max_workspace_bytes:
                raise GateStoreCapacityError(
                    f"Workspace limit exceeded: {self._active_workspace_bytes} + {requested_workspace_bytes} > {self.max_workspace_bytes}"
                )
            if self._active_spool_bytes + reserved_spool_bytes > self.max_result_spool_bytes:
                raise GateStoreCapacityError(
                    f"Result spool reservation exceeded: {self._active_spool_bytes} + {reserved_spool_bytes} > {self.max_result_spool_bytes}"
                )

            att_id = validate_safe_id(attempt_id, "attempt_id") if attempt_id else f"att_{uuid.uuid4().hex[:16]}"
            if att_id in self._attempts:
                raise GateStoreConflictError(f"Attempt ID {att_id!r} already exists")

            ws = self.workspaces_dir / att_id
            self._assert_safe_disposable_path(ws, self.workspaces_dir)
            ws.mkdir(mode=0o700, parents=True, exist_ok=True)
            sp = self.spool_dir / att_id
            self._assert_safe_disposable_path(sp, self.spool_dir)
            sp.mkdir(mode=0o700, parents=True, exist_ok=True)

            now = utc_now_iso()
            record: dict[str, Any] = {
                "format": "studio.developer-gate.status.v1",
                "acceptance": {
                    "attempt_id": att_id, "idempotency_key": idempotency_key,
                    "request_sha256": sha, "node_id": node_id, "controller_id": controller_id,
                },
                "state": "waiting_source", "reason": None, "accepted_at": now, "updated_at": now,
                "resource_release_confirmed": False, "result": None, "result_acknowledged": False,
                "workspace_removed": False, "inventory": None, "acknowledged_result": None,
                "acknowledged_at": None, "cleanup_after": None,
                "requested_workspace_bytes": requested_workspace_bytes,
                "reserved_spool_bytes": reserved_spool_bytes,
                "canonical_request_hex": canonical_request_bytes.hex(),
                "metadata": metadata or {},
            }

            atomic_write_json(self.attempts_dir / f"{att_id}.json", record)
            self._attempts[att_id] = record
            self._key_to_attempt[idempotency_key] = att_id
            self._active_workspace_bytes += requested_workspace_bytes
            self._active_spool_bytes += reserved_spool_bytes
            return copy.deepcopy(record)

    def get_attempt_by_id(self, attempt_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_open()
            if attempt_id in self._attempts:
                return copy.deepcopy(self._attempts[attempt_id])
        raise GateStoreNotFoundError(f"Attempt ID not found: {attempt_id!r}")

    def get_attempt_by_key(self, idempotency_key: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_open()
            if idempotency_key in self._key_to_attempt:
                return copy.deepcopy(self._attempts[self._key_to_attempt[idempotency_key]])
        raise GateStoreNotFoundError(f"Idempotency key not found: {idempotency_key!r}")

    def get_attempt(self, attempt_id_or_key: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_open()
            in_att = attempt_id_or_key in self._attempts
            in_key = attempt_id_or_key in self._key_to_attempt
            if in_att and in_key and self._attempts[attempt_id_or_key]["acceptance"]["idempotency_key"] != attempt_id_or_key:
                raise GateStoreConflictError(f"Ambiguous identifier: matches attempt ID and key of different attempts: {attempt_id_or_key!r}")
            if in_att:
                return copy.deepcopy(self._attempts[attempt_id_or_key])
            if in_key:
                return copy.deepcopy(self._attempts[self._key_to_attempt[attempt_id_or_key]])
        raise GateStoreNotFoundError(f"Attempt not found for identifier: {attempt_id_or_key!r}")

    def update_state(
        self,
        attempt_id: str,
        state: str,
        reason: dict[str, Any] | None = None,
        resource_release_confirmed: bool | None = None,
    ) -> dict[str, Any]:
        if state not in ALLOWED_STATES:
            raise GateStoreStateError(f"Invalid gate state: {state!r}")
        with self._lock:
            self._ensure_open()
            if attempt_id not in self._attempts:
                raise GateStoreNotFoundError(f"Attempt not found: {attempt_id!r}")
            rec = self._attempts[attempt_id]
            curr_state = rec["state"]

            if state != curr_state and state not in _VALID_TRANSITIONS.get(curr_state, set()):
                raise GateStoreStateError(f"Illegal state transition from {curr_state} to {state}")

            if state in TERMINAL_STATES and state != "not_run":
                if not rec.get("result"):
                    raise GateStoreStateError(f"Terminal state {state!r} requires durable result archive")
                if resource_release_confirmed is not True and not rec.get("resource_release_confirmed"):
                    raise GateStoreStateError(f"Terminal state {state!r} requires confirmed resource release")

            new_rec = copy.deepcopy(rec)
            new_rec["state"] = state
            if reason is not None:
                new_rec["reason"] = reason
            if resource_release_confirmed is not None:
                new_rec["resource_release_confirmed"] = bool(resource_release_confirmed)
            new_rec["updated_at"] = utc_now_iso()

            atomic_write_json(self.attempts_dir / f"{attempt_id}.json", new_rec)
            self._attempts[attempt_id] = new_rec
            return copy.deepcopy(new_rec)

    def persist_result_archive(
        self,
        attempt_id: str,
        stream_or_bytes: Any,
        expected_sha256: str | None = None,
        expected_size: int | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_open()
            if attempt_id not in self._attempts:
                raise GateStoreNotFoundError(f"Attempt not found: {attempt_id!r}")
            rec = self._attempts[attempt_id]
            if rec.get("result") is not None:
                ex = rec["result"]
                match_sha = (expected_sha256 is None or ex.get("sha256") == expected_sha256)
                match_sz = (expected_size is None or ex.get("size_bytes") == expected_size)
                if match_sha and match_sz:
                    return copy.deepcopy(ex)
                raise GateStoreConflictError(f"Result already persisted for attempt {attempt_id!r} with different archive")

            spool_sub = self.spool_dir / attempt_id
            self._assert_safe_disposable_path(spool_sub, self.spool_dir)
            spool_sub.mkdir(mode=0o700, parents=True, exist_ok=True)
            spool_tar = spool_sub / "result.tar"

            reserved = int(rec.get("reserved_spool_bytes", 0))
            attempt_limit = reserved if max_bytes is None else min(reserved, max_bytes)

            # Detect unexpected existing final before overwrite (e.g. rename succeeded previously but journal write failed)
            if spool_tar.exists():
                try:
                    sz_ex = spool_tar.stat().st_size
                except OSError as e:
                    raise GateStoreError(f"Failed to stat existing final archive for attempt {attempt_id!r}: {e}")
                if sz_ex > attempt_limit:
                    raise GateStoreCapacityError(
                        f"Existing final archive exceeds attempt reserved spool limit: {sz_ex} > {attempt_limit}"
                    )
                hasher_ex = hashlib.sha256()
                sz_read = 0
                with open(spool_tar, "rb") as ef:
                    while chunk := ef.read(65536):
                        hasher_ex.update(chunk)
                        sz_read += len(chunk)
                sz_ex = sz_read
                sha_ex = hasher_ex.hexdigest()

                matches = True
                if expected_sha256 is not None and expected_sha256 != sha_ex:
                    matches = False
                if expected_size is not None and expected_size != sz_ex:
                    matches = False
                if isinstance(stream_or_bytes, (bytes, bytearray)):
                    if hashlib.sha256(bytes(stream_or_bytes)).hexdigest() != sha_ex:
                        matches = False
                elif hasattr(stream_or_bytes, "seek") and hasattr(stream_or_bytes, "read"):
                    pos = stream_or_bytes.tell() if hasattr(stream_or_bytes, "tell") else 0
                    stream_hasher = hashlib.sha256()
                    stream_bytes = 0
                    while chunk := stream_or_bytes.read(65536):
                        stream_hasher.update(chunk)
                        stream_bytes += len(chunk)
                        if stream_bytes > sz_ex:
                            matches = False
                            break
                    stream_or_bytes.seek(pos)
                    if stream_bytes != sz_ex or stream_hasher.hexdigest() != sha_ex:
                        matches = False

                if not matches:
                    raise GateStoreConflictError(
                        f"Unexpected final archive already exists on disk for attempt {attempt_id!r} with different digest"
                    )

                descriptor = {"sha256": sha_ex, "size_bytes": sz_ex, "media_type": "application/x-tar"}
                new_rec = copy.deepcopy(rec)
                new_rec["result"] = descriptor
                new_rec["updated_at"] = utc_now_iso()
                atomic_write_json(self.attempts_dir / f"{attempt_id}.json", new_rec)
                self._attempts[attempt_id] = new_rec
                self._active_spool_bytes = self._derive_active_spool_bytes()
                return copy.deepcopy(descriptor)

            tmp_tar = spool_sub / f"result.tar.tmp.{uuid.uuid4().hex}"
            hasher = hashlib.sha256()
            bytes_written = 0

            # MUST respect accepted reserved_spool_bytes per attempt not borrow remaining other jobs evidence room
            available_spool = self.max_result_spool_bytes - (self._active_spool_bytes - reserved)

            try:
                fd = os.open(str(tmp_tar), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
                with open(fd, "wb") as out:
                    chunks = [bytes(stream_or_bytes)] if isinstance(stream_or_bytes, (bytes, bytearray)) else (
                        iter(lambda: stream_or_bytes.read(65536), b"") if hasattr(stream_or_bytes, "read") else stream_or_bytes
                    )
                    for chunk in chunks:
                        if not chunk:
                            continue
                        bytes_written += len(chunk)
                        if bytes_written > attempt_limit:
                            raise GateStoreCapacityError(f"Stream exceeded attempt reserved spool limit: {bytes_written} > {attempt_limit}")
                        if bytes_written > available_spool:
                            raise GateStoreCapacityError("Aggregate result spool capacity exceeded")
                        hasher.update(chunk)
                        out.write(chunk)
                    out.flush()
                    os.fsync(fd)

                actual_sha = hasher.hexdigest()
                if expected_size is not None and bytes_written != expected_size:
                    raise GateStoreError(f"Result size mismatch: actual {bytes_written} != expected {expected_size}")
                if expected_sha256 is not None and actual_sha != expected_sha256:
                    raise GateStoreError(f"Result SHA256 mismatch: actual {actual_sha} != expected {expected_sha256}")

                os.replace(tmp_tar, spool_tar)
                _fsync_dir(spool_sub)
                descriptor = {"sha256": actual_sha, "size_bytes": bytes_written, "media_type": "application/x-tar"}

                new_rec = copy.deepcopy(rec)
                new_rec["result"] = descriptor
                new_rec["updated_at"] = utc_now_iso()

                # If atomic_write_json fails, spool_tar is preserved on disk; retry will detect and reconcile it
                atomic_write_json(self.attempts_dir / f"{attempt_id}.json", new_rec)
                self._attempts[attempt_id] = new_rec
                self._active_spool_bytes = self._derive_active_spool_bytes()
                return copy.deepcopy(descriptor)
            except Exception:
                if tmp_tar.exists():
                    try:
                        tmp_tar.unlink()
                    except OSError:
                        pass
                raise

    def get_result_path(self, attempt_id: str) -> Path | None:
        with self._lock:
            self._ensure_open()
            if attempt_id not in self._attempts:
                raise GateStoreNotFoundError(f"Attempt not found: {attempt_id!r}")
            tar = self.spool_dir / attempt_id / "result.tar"
            if not tar.is_file() or os.path.islink(str(tar)):
                return None
            self._assert_safe_disposable_path(tar, self.spool_dir)
            return tar

    def ack_result(
        self,
        attempt_id_or_key: str,
        acceptance: dict[str, Any],
        result_descriptor: dict[str, Any],
        acknowledged_at: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_open()
            attempt = self.get_attempt(attempt_id_or_key)
            attempt_id = attempt["acceptance"]["attempt_id"]
            rec = self._attempts[attempt_id]
            acc = rec["acceptance"]

            required_acc_keys = {"attempt_id", "idempotency_key", "request_sha256", "node_id", "controller_id"}
            if set(acceptance.keys()) != required_acc_keys:
                raise GateStoreConflictError(f"ACK acceptance keys mismatch: {set(acceptance.keys())} != {required_acc_keys}")
            for k in required_acc_keys:
                if acceptance[k] != acc[k]:
                    raise GateStoreConflictError(f"ACK acceptance mismatch on {k}: {acceptance[k]!r} vs {acc[k]!r}")

            res = rec.get("result")
            if not res:
                raise GateStoreConflictError("Attempt has no recorded result to ACK")
            required_res_keys = {"sha256", "size_bytes", "media_type"}
            if set(result_descriptor.keys()) != required_res_keys:
                raise GateStoreConflictError(f"ACK result descriptor keys mismatch: {set(result_descriptor.keys())} != {required_res_keys}")
            for k in required_res_keys:
                if result_descriptor[k] != res[k]:
                    raise GateStoreConflictError(f"ACK result descriptor mismatch on {k}: {result_descriptor[k]!r} vs {res[k]!r}")

            tar = self.spool_dir / attempt_id / "result.tar"
            if rec.get("result_acknowledged") and not tar.exists():
                if rec.get("acknowledged_result") == result_descriptor:
                    return copy.deepcopy(rec)
                raise GateStoreConflictError("Attempt already acknowledged with different descriptor")

            ack_time = acknowledged_at or utc_now_iso()
            new_rec = copy.deepcopy(rec)
            new_rec["result_acknowledged"] = True
            new_rec["acknowledged_result"] = copy.deepcopy(result_descriptor)
            new_rec["acknowledged_at"] = rec.get("acknowledged_at") or ack_time
            new_rec["updated_at"] = ack_time

            atomic_write_json(self.attempts_dir / f"{attempt_id}.json", new_rec)
            self._attempts[attempt_id] = new_rec

            if tar.exists():
                self._assert_safe_disposable_path(tar, self.spool_dir)
                try:
                    tar.unlink()
                    _fsync_dir(tar.parent)
                    self._active_spool_bytes = self._derive_active_spool_bytes()
                except Exception as e:
                    new_rec["cleanup_failure"] = {"code": "spool_unlink_failed", "detail": "Spool unlink failed"}
                    atomic_write_json(self.attempts_dir / f"{attempt_id}.json", new_rec)
                    self._attempts[attempt_id] = new_rec
                    raise GateStoreError(f"Durable ACK persisted but spool unlink failed: {e}")

            return copy.deepcopy(new_rec)

    def _purge_disposable_workspace(
        self, attempt_id: str, rec: dict[str, Any], sample_cb: Callable[[], dict[str, Any]] | None
    ) -> dict[str, Any]:
        if rec.get("workspace_removed"):
            return copy.deepcopy(rec)

        ws = self.workspaces_dir / attempt_id
        if ws.exists():
            self._assert_safe_disposable_path(ws, self.workspaces_dir)
            shutil.rmtree(ws)
            _fsync_dir(self.workspaces_dir)
        new_rec = copy.deepcopy(rec)
        new_rec["workspace_removed"] = True
        new_rec["resource_release_confirmed"] = True
        new_rec["cleanup_after"] = sample_cb() if callable(sample_cb) else None
        new_rec["updated_at"] = utc_now_iso()

        atomic_write_json(self.attempts_dir / f"{attempt_id}.json", new_rec)
        self._attempts[attempt_id] = new_rec
        self._active_workspace_bytes = max(0, self._active_workspace_bytes - int(rec.get("requested_workspace_bytes", 0)))
        return copy.deepcopy(new_rec)

    def cleanup_workspace(
        self,
        attempt_id: str,
        process_release: dict[str, Any],
        sample_disk_callback: Callable[[], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_open()
            if attempt_id not in self._attempts:
                raise GateStoreNotFoundError(f"Attempt not found: {attempt_id!r}")
            rec = self._attempts[attempt_id]
            if rec.get("workspace_removed"):
                return copy.deepcopy(rec)
            self._verify_process_release(process_release)
            if not rec.get("result_acknowledged"):
                raise GateStoreStateError("Normal scratch removal requires durable result ACK before workspace removal")
            return self._purge_disposable_workspace(attempt_id, rec, sample_disk_callback)

    def reconcile_orphan(
        self,
        attempt_id: str,
        process_release: dict[str, Any],
        outcome: str = "interrupted",
        reason: dict[str, Any] | None = None,
        sample_disk_callback: Callable[[], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_open()
            if attempt_id not in self._attempts:
                raise GateStoreNotFoundError(f"Attempt not found: {attempt_id!r}")
            rec = self._attempts[attempt_id]

            # Idempotent: return unchanged if already removed; avoid subtract reservation twice
            if rec.get("workspace_removed"):
                return copy.deepcopy(rec)

            # 1. Verify process release; failclosed to recovery_hold
            try:
                self._verify_process_release(process_release)
            except Exception as e:
                new_rec = copy.deepcopy(rec)
                new_rec["state"] = "recovery_hold"
                new_rec["reason"] = reason or {"code": "recovery_hold", "detail": f"Process release verification failed: {type(e).__name__}"}
                new_rec["updated_at"] = utc_now_iso()
                atomic_write_json(self.attempts_dir / f"{attempt_id}.json", new_rec)
                self._attempts[attempt_id] = new_rec
                return copy.deepcopy(new_rec)

            # 2. Wire r2 section 8: Must refuse scratch removal unless durable verified spool/result exists first!
            res_desc = rec.get("result")
            if not res_desc or not isinstance(res_desc, dict) or "sha256" not in res_desc or "size_bytes" not in res_desc:
                new_rec = copy.deepcopy(rec)
                new_rec["state"] = "recovery_hold"
                new_rec["reason"] = {
                    "code": "missing_result",
                    "detail": "Orphan reconciliation cannot remove workspace: unreturned evidence has no verified result archive",
                }
                new_rec["updated_at"] = utc_now_iso()
                atomic_write_json(self.attempts_dir / f"{attempt_id}.json", new_rec)
                self._attempts[attempt_id] = new_rec
                return copy.deepcopy(new_rec)

            spool_path = self.get_result_path(attempt_id)
            if rec.get("result_acknowledged") and spool_path is None:
                # ACK tombstone exact descriptor allows already deleted result
                if rec.get("acknowledged_result") != res_desc:
                    new_rec = copy.deepcopy(rec)
                    new_rec["state"] = "recovery_hold"
                    new_rec["reason"] = {"code": "corrupt_ack_tombstone", "detail": "ACK tombstone does not match recorded result"}
                    new_rec["updated_at"] = utc_now_iso()
                    atomic_write_json(self.attempts_dir / f"{attempt_id}.json", new_rec)
                    self._attempts[attempt_id] = new_rec
                    return copy.deepcopy(new_rec)
            else:
                if spool_path is None or not spool_path.is_file():
                    new_rec = copy.deepcopy(rec)
                    new_rec["state"] = "recovery_hold"
                    new_rec["reason"] = {
                        "code": "missing_result_file",
                        "detail": "Orphan reconciliation cannot remove workspace: spool file missing on disk",
                    }
                    new_rec["updated_at"] = utc_now_iso()
                    atomic_write_json(self.attempts_dir / f"{attempt_id}.json", new_rec)
                    self._attempts[attempt_id] = new_rec
                    return copy.deepcopy(new_rec)

                # Gate actual removal on verified exact SHA256 and size on stored file; no destroy only evidence if spool corrupt
                hasher = hashlib.sha256()
                actual_size = 0
                try:
                    with open(spool_path, "rb") as sf:
                        while chunk := sf.read(65536):
                            hasher.update(chunk)
                            actual_size += len(chunk)
                except Exception as e:
                    new_rec = copy.deepcopy(rec)
                    new_rec["state"] = "recovery_hold"
                    new_rec["reason"] = {"code": "corrupt_spool_read_error", "detail": "Failed reading spool archive"}
                    new_rec["updated_at"] = utc_now_iso()
                    atomic_write_json(self.attempts_dir / f"{attempt_id}.json", new_rec)
                    self._attempts[attempt_id] = new_rec
                    return copy.deepcopy(new_rec)

                if actual_size != res_desc["size_bytes"] or hasher.hexdigest() != res_desc["sha256"]:
                    new_rec = copy.deepcopy(rec)
                    new_rec["state"] = "recovery_hold"
                    new_rec["reason"] = {
                        "code": "corrupt_result_archive",
                        "detail": "Spool archive checksum or size mismatch: evidence corrupted",
                    }
                    new_rec["updated_at"] = utc_now_iso()
                    atomic_write_json(self.attempts_dir / f"{attempt_id}.json", new_rec)
                    self._attempts[attempt_id] = new_rec
                    return copy.deepcopy(new_rec)

            if rec.get("state") not in TERMINAL_STATES:
                new_rec = copy.deepcopy(rec)
                new_rec["state"] = outcome
                new_rec["reason"] = reason or {"code": outcome, "detail": "Orphan reconciliation after confirmed process release"}
                new_rec["updated_at"] = utc_now_iso()
                atomic_write_json(self.attempts_dir / f"{attempt_id}.json", new_rec)
                self._attempts[attempt_id] = new_rec
                rec = new_rec

            return self._purge_disposable_workspace(attempt_id, rec, sample_disk_callback)

    def _verify_process_release(self, pr: dict[str, Any]) -> None:
        if not isinstance(pr, dict) or pr.get("confirmed") is not True or pr.get("owned_processes_remaining") != 0:
            raise GateStoreError("process_release must be a dict with confirmed=True and owned_processes_remaining=0")
        if pr.get("proof") not in ("never_started", "owned-process-identities-exited"):
            raise GateStoreError(f"Invalid process_release proof: {pr.get('proof')}")

    def get_status(
        self, attempt_id_or_key: str, inventory: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_open()
            attempt = self.get_attempt(attempt_id_or_key)
            inv = inventory if inventory is not None else attempt.get("inventory")
            return {
                "format": "studio.developer-gate.status.v1",
                "acceptance": copy.deepcopy(attempt["acceptance"]),
                "state": attempt["state"],
                "reason": copy.deepcopy(attempt["reason"]),
                "accepted_at": attempt["accepted_at"],
                "updated_at": attempt["updated_at"],
                "resource_release_confirmed": attempt["resource_release_confirmed"],
                "result": copy.deepcopy(attempt["result"]) if attempt["result"] else None,
                "result_acknowledged": attempt["result_acknowledged"],
                "workspace_removed": attempt["workspace_removed"],
                "inventory": copy.deepcopy(inv),
                "acknowledged_result": copy.deepcopy(attempt["acknowledged_result"]) if attempt["acknowledged_result"] else None,
                "acknowledged_at": attempt["acknowledged_at"],
                "cleanup_after": copy.deepcopy(attempt["cleanup_after"]) if attempt["cleanup_after"] else None,
            }

    def get_workspace_path(self, attempt_id: str) -> Path:
        with self._lock:
            self._ensure_open()
            attempt = self.get_attempt(attempt_id)
            if attempt.get("workspace_removed"):
                raise GateStoreNotFoundError(f"Workspace for attempt {attempt_id!r} has been removed")
            ws = self.workspaces_dir / attempt["acceptance"]["attempt_id"]
            self._assert_safe_disposable_path(ws, self.workspaces_dir)
            return ws

    def list_attempts(self) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_open()
            return [copy.deepcopy(a) for a in self._attempts.values()]

    @property
    def active_workspace_bytes(self) -> int:
        with self._lock:
            self._ensure_open()
            return self._active_workspace_bytes

    @property
    def active_spool_bytes(self) -> int:
        with self._lock:
            self._ensure_open()
            return self._active_spool_bytes
