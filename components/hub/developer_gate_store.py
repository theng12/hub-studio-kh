"""Durable per-attempt gate store and workspace lifecycle primitive.

Minimal standard-library storage for developer-gate attempts under an exclusive owned root.
Enforces safe path boundaries, atomic fsynced journals, idempotent identity reconciliation,
aggregate workspace and spool capacity accounting, exact-match ACK before result byte removal,
and guarded disposable scratch cleanup with confirmed process release.
"""

from __future__ import annotations

import datetime
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
    try:
        dfd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp.{uuid.uuid4().hex}"
    with open(tmp, "wb") as f:
        f.write(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True).encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


class DeveloperGateStore:
    def __init__(
        self,
        root_dir: str | Path,
        max_workspace_bytes: int = 40 * 1024**3,
        max_result_spool_bytes: int = 10 * 1024**3,
    ) -> None:
        raw_root = Path(root_dir)
        if os.path.islink(str(raw_root)):
            raise GateStorePathError(f"Root dir cannot be a symlink: {raw_root}")
        self.root_dir = raw_root.resolve()
        self.max_workspace_bytes = max_workspace_bytes
        self.max_result_spool_bytes = max_result_spool_bytes
        self.attempts_dir = self.root_dir / "attempts"
        self.workspaces_dir = self.root_dir / "workspaces"
        self.spool_dir = self.root_dir / "spool"
        self._lock = threading.RLock()
        self._attempts: dict[str, dict[str, Any]] = {}
        self._key_to_attempt: dict[str, str] = {}
        self._active_workspace_bytes: int = 0
        self._active_spool_bytes: int = 0
        self._init_storage()

    def _init_storage(self) -> None:
        self.attempts_dir.mkdir(parents=True, exist_ok=True)
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        for f in self.attempts_dir.glob("*.json"):
            if f.name.startswith("."):
                continue
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
                att_id = rec.get("acceptance", {}).get("attempt_id")
                key = rec.get("acceptance", {}).get("idempotency_key")
                if att_id and key:
                    self._attempts[att_id] = rec
                    self._key_to_attempt[key] = att_id
                    if not rec.get("workspace_removed"):
                        self._active_workspace_bytes += int(rec.get("requested_workspace_bytes", 0))
            except Exception:
                pass
        for d in self.spool_dir.iterdir():
            tar = d / "result.tar"
            if d.is_dir() and not os.path.islink(str(d)) and tar.is_file() and not os.path.islink(str(tar)):
                try:
                    self._active_spool_bytes += tar.stat().st_size
                except OSError:
                    pass

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
        requested_workspace_bytes: int = 0,
        attempt_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_safe_id(controller_id, "controller_id")
        validate_safe_id(node_id, "node_id")
        validate_safe_id(idempotency_key, "idempotency_key")
        if not isinstance(canonical_request_bytes, (bytes, bytearray)):
            raise TypeError("canonical_request_bytes must be bytes")
        computed_sha = compute_request_sha256(canonical_request_bytes)
        if request_sha256 is not None and request_sha256.lower() != computed_sha.lower():
            raise GateStoreConflictError(f"request_sha256 mismatch: {request_sha256} vs {computed_sha}")
        sha = computed_sha.lower()

        with self._lock:
            if idempotency_key in self._key_to_attempt:
                existing = self._attempts[self._key_to_attempt[idempotency_key]]
                ea = existing["acceptance"]
                if (
                    ea["controller_id"] == controller_id and ea["node_id"] == node_id
                    and ea["request_sha256"].lower() == sha
                    and existing.get("canonical_request_hex") == canonical_request_bytes.hex()
                ):
                    return dict(existing)
                raise GateStoreConflictError(f"Idempotency conflict for key {idempotency_key!r}: request identity differs")

            if self._active_workspace_bytes + requested_workspace_bytes > self.max_workspace_bytes:
                raise GateStoreCapacityError(
                    f"Workspace limit exceeded: {self._active_workspace_bytes} + {requested_workspace_bytes} > {self.max_workspace_bytes}"
                )

            att_id = validate_safe_id(attempt_id, "attempt_id") if attempt_id else f"att_{uuid.uuid4().hex[:16]}"
            if att_id in self._attempts:
                raise GateStoreConflictError(f"Attempt ID {att_id!r} already exists")

            ws = self.workspaces_dir / att_id
            self._assert_safe_disposable_path(ws, self.workspaces_dir)
            ws.mkdir(parents=True, exist_ok=True)
            sp = self.spool_dir / att_id
            self._assert_safe_disposable_path(sp, self.spool_dir)
            sp.mkdir(parents=True, exist_ok=True)

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
                "canonical_request_hex": canonical_request_bytes.hex(),
                "metadata": metadata or {},
            }
            atomic_write_json(self.attempts_dir / f"{att_id}.json", record)
            self._attempts[att_id] = record
            self._key_to_attempt[idempotency_key] = att_id
            self._active_workspace_bytes += requested_workspace_bytes
            return dict(record)

    def get_attempt(self, attempt_id_or_key: str) -> dict[str, Any]:
        with self._lock:
            if attempt_id_or_key in self._attempts:
                return dict(self._attempts[attempt_id_or_key])
            if attempt_id_or_key in self._key_to_attempt:
                return dict(self._attempts[self._key_to_attempt[attempt_id_or_key]])
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
            if attempt_id not in self._attempts:
                raise GateStoreNotFoundError(f"Attempt not found: {attempt_id!r}")
            rec = self._attempts[attempt_id]
            rec["state"] = state
            if reason is not None:
                rec["reason"] = reason
            if resource_release_confirmed is not None:
                rec["resource_release_confirmed"] = bool(resource_release_confirmed)
            rec["updated_at"] = utc_now_iso()
            atomic_write_json(self.attempts_dir / f"{attempt_id}.json", rec)
            return dict(rec)

    def persist_result_archive(
        self,
        attempt_id: str,
        stream_or_bytes: Any,
        expected_sha256: str | None = None,
        expected_size: int | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if attempt_id not in self._attempts:
                raise GateStoreNotFoundError(f"Attempt not found: {attempt_id!r}")
            rec = self._attempts[attempt_id]
            if rec.get("result") is not None:
                if expected_sha256 and rec["result"].get("sha256") == expected_sha256.lower():
                    return dict(rec["result"])
                raise GateStoreConflictError(f"Result already persisted for attempt {attempt_id!r} with different archive")

            spool_sub = self.spool_dir / attempt_id
            self._assert_safe_disposable_path(spool_sub, self.spool_dir)
            spool_sub.mkdir(parents=True, exist_ok=True)
            spool_tar = spool_sub / "result.tar"
            tmp_tar = spool_sub / f"result.tar.tmp.{uuid.uuid4().hex}"
            hasher = hashlib.sha256()
            bytes_written = 0

            try:
                with open(tmp_tar, "wb") as out:
                    chunks = [bytes(stream_or_bytes)] if isinstance(stream_or_bytes, (bytes, bytearray)) else (
                        iter(lambda: stream_or_bytes.read(65536), b"") if hasattr(stream_or_bytes, "read") else stream_or_bytes
                    )
                    for chunk in chunks:
                        if not chunk:
                            continue
                        bytes_written += len(chunk)
                        if max_bytes is not None and bytes_written > max_bytes:
                            raise GateStoreCapacityError(f"Stream exceeded archive limit: {bytes_written} > {max_bytes}")
                        if self._active_spool_bytes + bytes_written > self.max_result_spool_bytes:
                            raise GateStoreCapacityError("Aggregate result spool capacity exceeded")
                        hasher.update(chunk)
                        out.write(chunk)
                    out.flush()
                    os.fsync(out.fileno())

                actual_sha = hasher.hexdigest().lower()
                if expected_size is not None and bytes_written != expected_size:
                    raise GateStoreError(f"Result size mismatch: actual {bytes_written} != expected {expected_size}")
                if expected_sha256 is not None and actual_sha != expected_sha256.lower():
                    raise GateStoreError(f"Result SHA256 mismatch: actual {actual_sha} != expected {expected_sha256}")

                os.replace(tmp_tar, spool_tar)
                _fsync_dir(spool_sub)
                descriptor = {"sha256": actual_sha, "size_bytes": bytes_written, "media_type": "application/x-tar"}
                rec["result"] = descriptor
                rec["updated_at"] = utc_now_iso()
                atomic_write_json(self.attempts_dir / f"{attempt_id}.json", rec)
                self._active_spool_bytes += bytes_written
                return dict(descriptor)
            except Exception:
                if tmp_tar.exists():
                    try:
                        tmp_tar.unlink()
                    except OSError:
                        pass
                raise

    def get_result_path(self, attempt_id: str) -> Path | None:
        with self._lock:
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
            attempt = self.get_attempt(attempt_id_or_key)
            attempt_id = attempt["acceptance"]["attempt_id"]
            rec = self._attempts[attempt_id]
            acc = rec["acceptance"]
            for k in ("attempt_id", "idempotency_key", "request_sha256", "node_id", "controller_id"):
                if str(acceptance.get(k, "")).lower() != str(acc.get(k, "")).lower():
                    raise GateStoreConflictError(f"ACK acceptance mismatch on {k}: {acceptance.get(k)} vs {acc.get(k)}")
            res = rec.get("result")
            if not res or (
                res.get("sha256", "").lower() != str(result_descriptor.get("sha256", "")).lower()
                or res.get("size_bytes") != result_descriptor.get("size_bytes")
                or res.get("media_type") != result_descriptor.get("media_type")
            ):
                raise GateStoreConflictError(f"ACK result descriptor mismatch: {result_descriptor} vs {res}")
            if rec.get("result_acknowledged"):
                if rec.get("acknowledged_result") == result_descriptor:
                    return dict(rec)
                raise GateStoreConflictError("Attempt already acknowledged with different descriptor")

            ack_time = acknowledged_at or utc_now_iso()
            rec["result_acknowledged"] = True
            rec["acknowledged_result"] = dict(result_descriptor)
            rec["acknowledged_at"] = ack_time
            rec["updated_at"] = ack_time

            # MUST persist true timestamp durably BEFORE removing result bytes
            atomic_write_json(self.attempts_dir / f"{attempt_id}.json", rec)

            tar = self.spool_dir / attempt_id / "result.tar"
            if tar.exists():
                self._assert_safe_disposable_path(tar, self.spool_dir)
                try:
                    freed = tar.stat().st_size
                    tar.unlink()
                    self._active_spool_bytes = max(0, self._active_spool_bytes - freed)
                    _fsync_dir(tar.parent)
                except OSError:
                    pass
            return dict(rec)

    def _purge_disposable_workspace(
        self, attempt_id: str, rec: dict[str, Any], sample_cb: Callable[[], dict[str, Any]] | None
    ) -> None:
        ws = self.workspaces_dir / attempt_id
        if ws.exists():
            self._assert_safe_disposable_path(ws, self.workspaces_dir)
            shutil.rmtree(ws)
            _fsync_dir(self.workspaces_dir)
        rec["workspace_removed"] = True
        rec["resource_release_confirmed"] = True
        rec["cleanup_after"] = sample_cb() if callable(sample_cb) else None
        rec["updated_at"] = utc_now_iso()
        self._active_workspace_bytes = max(0, self._active_workspace_bytes - int(rec.get("requested_workspace_bytes", 0)))
        atomic_write_json(self.attempts_dir / f"{attempt_id}.json", rec)

    def cleanup_workspace(
        self,
        attempt_id: str,
        process_release: dict[str, Any],
        sample_disk_callback: Callable[[], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if attempt_id not in self._attempts:
                raise GateStoreNotFoundError(f"Attempt not found: {attempt_id!r}")
            rec = self._attempts[attempt_id]
            if rec.get("workspace_removed"):
                return dict(rec)
            self._verify_process_release(process_release)
            if not rec.get("result_acknowledged"):
                raise GateStoreStateError("Normal scratch removal requires durable result ACK before workspace removal")
            self._purge_disposable_workspace(attempt_id, rec, sample_disk_callback)
            return dict(rec)

    def reconcile_orphan(
        self,
        attempt_id: str,
        process_release: dict[str, Any],
        outcome: str = "interrupted",
        reason: dict[str, Any] | None = None,
        sample_disk_callback: Callable[[], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if attempt_id not in self._attempts:
                raise GateStoreNotFoundError(f"Attempt not found: {attempt_id!r}")
            rec = self._attempts[attempt_id]
            try:
                self._verify_process_release(process_release)
            except Exception as e:
                rec["state"] = "recovery_hold"
                rec["reason"] = reason or {"code": "recovery_hold", "detail": f"Process release verification failed: {e}"}
                rec["updated_at"] = utc_now_iso()
                atomic_write_json(self.attempts_dir / f"{attempt_id}.json", rec)
                return dict(rec)
            if rec.get("state") not in TERMINAL_STATES:
                rec["state"] = outcome
                rec["reason"] = reason or {"code": outcome, "detail": "Orphan reconciliation after confirmed process release"}
            rec["updated_at"] = utc_now_iso()
            atomic_write_json(self.attempts_dir / f"{attempt_id}.json", rec)
            # NOTE: Spool is preserved; disposable scratch workspace purged
            self._purge_disposable_workspace(attempt_id, rec, sample_disk_callback)
            return dict(rec)

    def _verify_process_release(self, pr: dict[str, Any]) -> None:
        if not isinstance(pr, dict) or pr.get("confirmed") is not True or pr.get("owned_processes_remaining") != 0:
            raise GateStoreError("process_release must be a dict with confirmed=True and owned_processes_remaining=0")
        if pr.get("proof") not in ("never_started", "owned-process-identities-exited"):
            raise GateStoreError(f"Invalid process_release proof: {pr.get('proof')}")

    def get_status(
        self, attempt_id_or_key: str, inventory: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        attempt = self.get_attempt(attempt_id_or_key)
        inv = inventory if inventory is not None else attempt.get("inventory")
        return {
            "format": "studio.developer-gate.status.v1",
            "acceptance": dict(attempt["acceptance"]),
            "state": attempt["state"],
            "reason": attempt["reason"],
            "accepted_at": attempt["accepted_at"],
            "updated_at": attempt["updated_at"],
            "resource_release_confirmed": attempt["resource_release_confirmed"],
            "result": dict(attempt["result"]) if attempt["result"] else None,
            "result_acknowledged": attempt["result_acknowledged"],
            "workspace_removed": attempt["workspace_removed"],
            "inventory": inv,
            "acknowledged_result": dict(attempt["acknowledged_result"]) if attempt["acknowledged_result"] else None,
            "acknowledged_at": attempt["acknowledged_at"],
            "cleanup_after": dict(attempt["cleanup_after"]) if attempt["cleanup_after"] else None,
        }

    def get_workspace_path(self, attempt_id: str) -> Path:
        attempt = self.get_attempt(attempt_id)
        if attempt.get("workspace_removed"):
            raise GateStoreNotFoundError(f"Workspace for attempt {attempt_id!r} has been removed")
        ws = self.workspaces_dir / attempt["acceptance"]["attempt_id"]
        self._assert_safe_disposable_path(ws, self.workspaces_dir)
        return ws

    def list_attempts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(a) for a in self._attempts.values()]

    @property
    def active_workspace_bytes(self) -> int:
        with self._lock:
            return self._active_workspace_bytes

    @property
    def active_spool_bytes(self) -> int:
        with self._lock:
            return self._active_spool_bytes
