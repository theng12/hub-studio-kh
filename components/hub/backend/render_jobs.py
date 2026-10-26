"""Durable internal lifecycle for closed private final-render requests.

This module records acceptance and process ownership.  It deliberately does not
route HTTP, qualify hardware, acquire the broker's global physical lease, spawn or
reap a process, compile a command, inspect media, or reclaim files.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from . import ledger
from .render_contract import validate_render_packet_syntax
from .render_hash_v1 import decode_render_json

MAX_REQUEST_BYTES = 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ASSET_HANDLE = re.compile(r"[0-9a-f]{32}\Z")
_FILE_LEASE_KINDS = frozenset({"reference", "reader", "transfer", "ack"})
_DISPATCH_KEYS = frozenset({
    "story_uuid", "intent_id", "attempt_no", "gateway_attempt_key",
    "destination_site", "destination_controller", "destination_node",
    "dispatch_key", "dispatch_fence",
})
#: The optional labels Story Studio may send so the Render tab can name a render instead of
#: printing its job id. Display only: nothing routes, dispatches, hashes, or accepts on them.
_DISPLAY_KEYS = frozenset({"channel", "episode"})
_DISPLAY_MAX_CHARACTERS = 256

_SCHEMA = """
CREATE TABLE IF NOT EXISTS render_jobs (
  id TEXT PRIMARY KEY,
  owner_scope TEXT NOT NULL,
  dispatch_key TEXT NOT NULL,
  body_sha256 TEXT NOT NULL,
  request_bytes BLOB NOT NULL,
  packet_json TEXT NOT NULL,
  story_uuid TEXT NOT NULL,
  intent_id TEXT NOT NULL,
  attempt_no INTEGER NOT NULL,
  gateway_attempt_key TEXT NOT NULL,
  destination_site TEXT NOT NULL,
  destination_controller TEXT NOT NULL,
  destination_node TEXT NOT NULL,
  dispatch_fence TEXT NOT NULL,
  profile_digest TEXT NOT NULL,
  tools_digest TEXT NOT NULL,
  compiler_digest TEXT NOT NULL,
  qualification_digest TEXT NOT NULL,
  qualification_json TEXT NOT NULL,
  purpose TEXT NOT NULL DEFAULT 'production',
  qualification_permit_json TEXT,
  state TEXT NOT NULL,
  machine TEXT,
  dispatch_generation INTEGER,
  attempt_id TEXT,
  launch_token TEXT,
  workspace TEXT,
  output_path TEXT,
  compute_held INTEGER NOT NULL DEFAULT 0,
  process_pid INTEGER,
  process_group_id INTEGER,
  process_start_token TEXT,
  stopped_evidence_json TEXT,
  stopped_evidence_digest TEXT,
  validation_pending INTEGER NOT NULL DEFAULT 0,
  output_receipt_json TEXT,
  output_receipt_digest TEXT,
  cancel_requested_at REAL,
  recovery_note TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  -- When the render process was bound. `updated_at` is the other end (the row's last
  -- lifecycle write, which for a terminal row is its settlement), so the two give the Render
  -- tab a measured wall time. Null for a row accepted before this column existed: that render
  -- reports no wall time rather than an invented one.
  started_at REAL,
  -- The channel and episode labels Story Studio sent for the Render tab (owner, 2026-10-26).
  -- Display only, and optional: a request without it still names its render by job id, and no
  -- dispatch, routing, hashing or acceptance decision reads this column.
  display_json TEXT,
  UNIQUE(owner_scope, dispatch_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_render_one_compute_per_machine
  ON render_jobs(machine) WHERE compute_held = 1 AND machine IS NOT NULL;
CREATE TABLE IF NOT EXISTS render_file_leases (
  job_id TEXT NOT NULL,
  lease_kind TEXT NOT NULL,
  lease_id TEXT NOT NULL,
  active INTEGER NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY(job_id, lease_kind, lease_id),
  FOREIGN KEY(job_id) REFERENCES render_jobs(id)
);
CREATE TABLE IF NOT EXISTS render_cancel_fences (
  owner_scope TEXT NOT NULL,
  dispatch_key TEXT NOT NULL,
  expected_request_sha256 TEXT NOT NULL,
  fence_id TEXT NOT NULL,
  cancelled_at REAL NOT NULL,
  PRIMARY KEY(owner_scope, dispatch_key)
);
CREATE TABLE IF NOT EXISTS render_qualification_vectors (
  vector_id TEXT PRIMARY KEY,
  owner_scope TEXT NOT NULL,
  dispatch_key TEXT NOT NULL,
  body_sha256 TEXT NOT NULL,
  job_id TEXT NOT NULL,
  consumed_at REAL NOT NULL
);
"""


class RenderJobConflict(ValueError):
    """The requested identity or transition conflicts with durable state."""


class RenderJobUnavailable(RuntimeError):
    """A fresh acceptance or launch lacks current server-owned authority."""


class RenderJobNotFound(LookupError):
    """No job is visible in the supplied owner scope."""


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 value")
    return value


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 2**63 - 1:
        raise ValueError(f"{name} must be a positive signed-63-bit integer")
    return value


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 2**63 - 1:
        raise ValueError(f"{name} must be a non-negative signed-63-bit integer")
    return value


def _text(value: Any, name: str, *, maximum: int = 256) -> str:
    if (not isinstance(value, str) or not value or len(value) > maximum
            or not value.isascii() or not all(" " <= char <= "~" for char in value)):
        raise ValueError(f"{name} must be 1-{maximum} printable ASCII characters")
    return value


def _label(value: Any, name: str, *, maximum: int = 256) -> str:
    """A compiler-issued display label: bounded, non-empty, no control characters.

    Unlike ``_text`` this admits non-ASCII. The pinned compiler's own tier labels contain
    ``\u00b7`` ("H.264 \u00b7 preset medium"), so demanding ASCII refused the receipt of every
    real render at output validation. The label is still compared exactly (to the capability
    row and the machine's authority) and hashed in the receipt digest, so nothing is let through
    that was not already matched byte for byte.
    """
    if (not isinstance(value, str) or not value or len(value) > maximum
            or value != value.strip() or not value.isprintable()):
        raise ValueError(f"{name} must be 1-{maximum} printable characters")
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _display(value: Any) -> dict[str, str]:
    """The optional display-only labels: which channel and episode a render belongs to.

    Both keys are optional and nothing downstream decides anything with them. A label is
    bounded and NUL-free, and — unlike ``_text`` — admits non-ASCII, because a channel or
    episode name is the customer's own words and may be in any script. Nothing stricter than
    that is enforced: refusing a title over a character would turn a display label into a
    reason a render is not accepted, which is exactly what display must never do.
    """
    if type(value) is not dict or not value or not set(value) <= _DISPLAY_KEYS:
        raise ValueError("render display has missing or unknown fields")
    for name, label in value.items():
        if (not isinstance(label, str) or not label
                or len(label) > _DISPLAY_MAX_CHARACTERS or "\x00" in label):
            raise ValueError(
                f"display {name} must be 1-{_DISPLAY_MAX_CHARACTERS} characters without NUL")
    return value


def decode_render_request(
    raw_request: bytes, hub_dispatch_key: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Decode the one closed public request shape without consulting mutable runtime state."""
    if not isinstance(raw_request, bytes):
        raise TypeError("render request must be bytes")
    if len(raw_request) > MAX_REQUEST_BYTES:
        raise ValueError("render request is too large")
    try:
        value = decode_render_json(raw_request)
    except ValueError as exc:
        raise ValueError("invalid render request: " + str(exc)) from exc
    if (type(value) is not dict
            or set(value) - {"display"} != {"format", "dispatch", "packet", "assets"}):
        raise ValueError("render request has missing or unknown fields")
    if value["format"] != "studio.render.hub-request.v1":
        raise ValueError("render request format is unsupported")
    if "display" in value:
        _display(value["display"])
    dispatch = value["dispatch"]
    if type(dispatch) is not dict or set(dispatch) != _DISPATCH_KEYS:
        raise ValueError("render dispatch has missing or unknown fields")
    for name in _DISPATCH_KEYS - {"attempt_no"}:
        _text(dispatch[name], name)
    _positive_int(dispatch["attempt_no"], "attempt_no")
    if dispatch["dispatch_key"] != hub_dispatch_key:
        raise ValueError("hub dispatch_key does not match the request")
    packet_raw = _canonical(value["packet"]).encode("utf-8")
    packet = validate_render_packet_syntax(packet_raw)
    assets = value["assets"]
    expected = {entry["logical_id"] for entry in packet["manifest"]}
    if type(assets) is not dict or set(assets) != expected:
        raise ValueError("render asset mapping must exactly match packet logical references")
    if any(not isinstance(handle, str) or _ASSET_HANDLE.fullmatch(handle) is None
           for handle in assets.values()):
        raise ValueError("render asset mapping contains an invalid scoped handle")
    return value, packet, _canonical(packet)


@dataclass(frozen=True)
class RuntimeQualification:
    profile_digest: str
    tools_digest: str
    compiler_digest: str
    qualification_digest: str
    hardware_class: str
    production_eligible: bool
    min_free_bytes: int
    scratch_reserve_bytes: int
    output_limit_bytes: int
    # The exact authority document this qualification was derived from. It is stored with
    # the job (qualification_json) so a claimed job keeps its authority after the operator
    # revokes or deletes the file it came from.
    document: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        for name in ("profile_digest", "tools_digest", "compiler_digest",
                     "qualification_digest"):
            _digest(getattr(self, name), name)
        _text(self.hardware_class, "hardware_class")
        if type(self.production_eligible) is not bool:
            raise ValueError("production_eligible must be a boolean")
        if self.document is not None and type(self.document) is not dict:
            raise ValueError("qualification document must be a JSON object")
        _nonnegative_int(self.min_free_bytes, "min_free_bytes")
        _nonnegative_int(self.scratch_reserve_bytes, "scratch_reserve_bytes")
        _positive_int(self.output_limit_bytes, "output_limit_bytes")


@dataclass(frozen=True)
class QualificationPermit:
    """Server-derived authority for one exact qualification request.

    ``vector_id`` carries the operator's qualify.json ``intent_id`` (the wire header kept
    its vector name); ``intent_id`` is the Story dispatch intent.
    """

    vector_id: str
    owner_scope: str
    exact_request_sha256: str
    qualification_digest: str
    hardware_class: str
    story_uuid: str
    intent_id: str
    attempt_no: int
    gateway_attempt_key: str
    destination_site: str
    destination_controller: str
    destination_node: str
    dispatch_key: str
    dispatch_fence: str

    def __post_init__(self) -> None:
        for name in (
            "vector_id", "owner_scope", "hardware_class", "story_uuid", "intent_id",
            "gateway_attempt_key", "destination_site", "destination_controller",
            "destination_node", "dispatch_key", "dispatch_fence",
        ):
            _text(getattr(self, name), name)
        _digest(self.exact_request_sha256, "exact_request_sha256")
        _digest(self.qualification_digest, "qualification_digest")
        _positive_int(self.attempt_no, "attempt_no")


@dataclass(frozen=True)
class CancelledBeforeAcceptanceReceipt:
    owner_scope: str
    dispatch_key: str
    expected_request_sha256: str
    fence_id: str
    cancelled_at: float
    state: str = "cancelled_before_acceptance"

    def __post_init__(self) -> None:
        _text(self.owner_scope, "owner_scope")
        _text(self.dispatch_key, "dispatch_key")
        _digest(self.expected_request_sha256, "expected_request_sha256")
        if not isinstance(self.fence_id, str) or _ASSET_HANDLE.fullmatch(self.fence_id) is None:
            raise ValueError("fence_id must be a lowercase 128-bit value")
        if (isinstance(self.cancelled_at, bool)
                or not isinstance(self.cancelled_at, (int, float))
                or not math.isfinite(self.cancelled_at) or self.cancelled_at < 0):
            raise ValueError("cancelled_at must be finite non-negative seconds")
        if self.state != "cancelled_before_acceptance":
            raise ValueError("cancel fence state is immutable")


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    process_group_id: int
    start_token: str

    def __post_init__(self) -> None:
        _positive_int(self.pid, "pid")
        _positive_int(self.process_group_id, "process_group_id")
        _text(self.start_token, "start_token", maximum=512)


@dataclass(frozen=True)
class StoppedProcessEvidence:
    process: ProcessIdentity
    observed_at: float
    exit_code: int
    reason: str

    def __post_init__(self) -> None:
        if type(self.process) is not ProcessIdentity:
            raise TypeError("process stop evidence requires a trusted ProcessIdentity")
        if (isinstance(self.observed_at, bool)
                or not isinstance(self.observed_at, (int, float))
                or not math.isfinite(self.observed_at) or self.observed_at < 0):
            raise ValueError("observed_at must be finite non-negative seconds")
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ValueError("exit_code must be an integer")
        if self.reason not in {"completed", "cancelled", "failed"}:
            raise ValueError("stop reason is unsupported")


@dataclass(frozen=True)
class ValidatedOutputReceipt:
    owner_scope: str
    hub_job_id: str
    attempt_id: str
    accepted_request_sha256: str
    receipt_digest: str
    output_sha256: str
    output_size_bytes: int
    profile_digest: str
    tools_digest: str
    compiler_digest: str
    qualification_digest: str
    generated_command_digest: str
    selected_tier: str
    feature_losses: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.owner_scope, "owner_scope")
        _text(self.hub_job_id, "hub_job_id")
        _text(self.attempt_id, "attempt_id")
        for name in ("accepted_request_sha256", "receipt_digest", "output_sha256",
                     "profile_digest", "tools_digest", "compiler_digest",
                     "qualification_digest", "generated_command_digest"):
            _digest(getattr(self, name), name)
        _positive_int(self.output_size_bytes, "output_size_bytes")
        _label(self.selected_tier, "selected_tier")
        if (type(self.feature_losses) is not tuple
                or any(not isinstance(value, str) or not value or len(value) > 256
                       for value in self.feature_losses)):
            raise ValueError("feature_losses must be a tuple of bounded non-empty strings")


@dataclass(frozen=True)
class RenderExecutionContext:
    """Immutable server-only inputs for the one already-claimed attempt."""

    owner_scope: str
    hub_job_id: str
    attempt_id: str
    launch_token: str
    accepted_request_sha256: str
    raw_request: bytes
    packet: dict[str, Any]
    assets: dict[str, str]
    qualification: RuntimeQualification
    qualification_permit: QualificationPermit | None
    purpose: str
    workspace: str
    output_path: str
    machine: str
    dispatch_generation: int


class RenderJobRuntime:
    def __init__(
        self,
        data_root: str | Path,
        *,
        database_path: str | Path = ledger.DB_FILE,
        qualification_resolver: Callable[[dict], RuntimeQualification | None] | None = None,
        free_bytes: Callable[[Path], int] | None = None,
    ) -> None:
        self.root = Path(data_root).absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._qualification_resolver = qualification_resolver or (lambda _packet: None)
        self._free_bytes = free_bytes or (lambda path: shutil.disk_usage(path).free)
        with self._conn():
            pass

    @contextmanager
    def _conn(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(_SCHEMA)
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(render_jobs)")
            }
            if "purpose" not in columns:
                connection.execute(
                    "ALTER TABLE render_jobs ADD COLUMN purpose TEXT NOT NULL DEFAULT 'production'")
            if "qualification_permit_json" not in columns:
                connection.execute(
                    "ALTER TABLE render_jobs ADD COLUMN qualification_permit_json TEXT")
            if "validation_pending" not in columns:
                connection.execute(
                    "ALTER TABLE render_jobs ADD COLUMN validation_pending "
                    "INTEGER NOT NULL DEFAULT 0")
            if "started_at" not in columns:
                # Added for the Render tab (owner, 2026-10-26). An existing row keeps NULL: the
                # fact was never recorded, so the history reports it as unknown, not as zero.
                connection.execute(
                    "ALTER TABLE render_jobs ADD COLUMN started_at REAL")
            if "display_json" not in columns:
                # Added for the Render tab (owner, 2026-10-26). A row accepted before this keeps
                # NULL: Story Studio never sent a name, so the tab says what it does know (the job
                # id) rather than inventing one.
                connection.execute(
                    "ALTER TABLE render_jobs ADD COLUMN display_json TEXT")
            for row in connection.execute(
                    "SELECT id,owner_scope,dispatch_key,body_sha256,"
                    "qualification_permit_json,created_at FROM render_jobs "
                    "WHERE qualification_permit_json IS NOT NULL ORDER BY created_at,id"):
                try:
                    vector_id = json.loads(row["qualification_permit_json"])["vector_id"]
                    _text(vector_id, "vector_id")
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                connection.execute(
                    "INSERT OR IGNORE INTO render_qualification_vectors "
                    "(vector_id,owner_scope,dispatch_key,body_sha256,job_id,consumed_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (vector_id, row["owner_scope"], row["dispatch_key"],
                     row["body_sha256"], row["id"], row["created_at"]),
                )
            connection.commit()
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _begin(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _owned(connection: sqlite3.Connection, owner_scope: str, job_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM render_jobs WHERE id = ? AND owner_scope = ?",
            (job_id, owner_scope),
        ).fetchone()
        if row is None:
            raise RenderJobNotFound("render job not found")
        return row

    @staticmethod
    def _lease_counts(connection: sqlite3.Connection, job_id: str) -> dict[str, Any]:
        counts = {kind: 0 for kind in _FILE_LEASE_KINDS}
        for row in connection.execute(
            "SELECT lease_kind, COUNT(*) AS count FROM render_file_leases "
            "WHERE job_id = ? AND active = 1 GROUP BY lease_kind", (job_id,),
        ):
            counts[row["lease_kind"]] = int(row["count"])
        return counts

    @classmethod
    def _public(cls, connection: sqlite3.Connection, row: sqlite3.Row,
                *, replayed: bool = False) -> dict[str, Any]:
        result = {
            "id": row["id"],
            "owner_scope": row["owner_scope"],
            "dispatch_key": row["dispatch_key"],
            "accepted_request_sha256": row["body_sha256"],
            "purpose": row["purpose"],
            "state": row["state"],
            "machine": row["machine"],
            "dispatch_generation": row["dispatch_generation"],
            "attempt_id": row["attempt_id"],
            "cancel_requested_at": row["cancel_requested_at"],
            "profile_digest": row["profile_digest"],
            "tools_digest": row["tools_digest"],
            "compiler_digest": row["compiler_digest"],
            "qualification_digest": row["qualification_digest"],
            "stopped_evidence_digest": row["stopped_evidence_digest"],
            "output_receipt": (
                json.loads(row["output_receipt_json"])
                if row["output_receipt_json"] is not None else None
            ),
            "leases": {
                "compute": bool(row["compute_held"]),
                **cls._lease_counts(connection, row["id"]),
            },
            "replayed": replayed,
        }
        return result

    @staticmethod
    def _row_identity(row: sqlite3.Row) -> ProcessIdentity | None:
        """The recorded process, or None when no complete identity was durably bound."""
        values = (row["process_pid"], row["process_group_id"], row["process_start_token"])
        if any(value is None for value in values):
            return None
        try:
            return ProcessIdentity(
                pid=values[0], process_group_id=values[1], start_token=values[2])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _current_qualification(
        resolver: Callable[[dict], RuntimeQualification | None], packet: dict,
        *, allow_candidate: bool = False,
    ) -> RuntimeQualification:
        authority = resolver(packet)
        if (type(authority) is not RuntimeQualification
                or (not allow_candidate and not authority.production_eligible)):
            raise RenderJobUnavailable("no server-qualified render runtime is available")
        if authority.profile_digest != packet["recipe"]["profile_digest"]:
            raise RenderJobUnavailable("qualified render profile does not match the packet")
        return authority

    @staticmethod
    def _permit_for(
        permit: QualificationPermit,
        *, owner_scope: str,
        raw_request: bytes,
        dispatch: dict[str, Any],
        authority: RuntimeQualification,
    ) -> str:
        if type(permit) is not QualificationPermit:
            raise TypeError("qualification authority requires a trusted QualificationPermit")
        expected = (
            owner_scope, hashlib.sha256(raw_request).hexdigest(), authority.qualification_digest,
            authority.hardware_class, dispatch["story_uuid"], dispatch["intent_id"],
            dispatch["attempt_no"], dispatch["gateway_attempt_key"],
            dispatch["destination_site"], dispatch["destination_controller"],
            dispatch["destination_node"], dispatch["dispatch_key"],
            dispatch["dispatch_fence"],
        )
        actual = (
            permit.owner_scope, permit.exact_request_sha256, permit.qualification_digest,
            permit.hardware_class, permit.story_uuid, permit.intent_id, permit.attempt_no,
            permit.gateway_attempt_key, permit.destination_site,
            permit.destination_controller, permit.destination_node, permit.dispatch_key,
            permit.dispatch_fence,
        )
        if actual != expected:
            raise RenderJobUnavailable("qualification permit does not bind this exact request")
        return _canonical(asdict(permit))

    def accept(
        self,
        owner_scope: str,
        hub_dispatch_key: str,
        raw_request: bytes,
        *,
        qualification_permit: QualificationPermit | None = None,
    ) -> dict[str, Any]:
        owner_scope = _text(owner_scope, "owner_scope")
        hub_dispatch_key = _text(hub_dispatch_key, "dispatch_key")
        value, packet, packet_json = decode_render_request(raw_request, hub_dispatch_key)
        body_sha256 = hashlib.sha256(raw_request).hexdigest()
        dispatch = value["dispatch"]
        with self._conn() as connection:
            self._begin(connection)
            existing = connection.execute(
                "SELECT * FROM render_jobs WHERE owner_scope = ? AND dispatch_key = ?",
                (owner_scope, hub_dispatch_key),
            ).fetchone()
            if existing is not None:
                if existing["body_sha256"] != body_sha256:
                    raise RenderJobConflict(
                        "render dispatch key was already accepted for a different exact body")
                return self._public(connection, existing, replayed=True)
            fence = connection.execute(
                "SELECT * FROM render_cancel_fences WHERE owner_scope = ? AND dispatch_key = ?",
                (owner_scope, hub_dispatch_key),
            ).fetchone()
            if fence is not None:
                if fence["expected_request_sha256"] != body_sha256:
                    raise RenderJobConflict(
                        "render dispatch key was cancelled for a different exact body")
                raise RenderJobUnavailable(
                    "render dispatch was cancelled before acceptance")
            authority = self._current_qualification(
                self._qualification_resolver, packet,
                allow_candidate=qualification_permit is not None,
            )
            permit_json = None
            purpose = "production"
            if qualification_permit is not None:
                permit_json = self._permit_for(
                    qualification_permit, owner_scope=owner_scope,
                    raw_request=raw_request, dispatch=dispatch, authority=authority,
                )
                purpose = "qualification"
            now = time.time()
            job_id = str(uuid.uuid4())
            if qualification_permit is not None:
                # The vector_id column holds the operator's qualify.json intent_id: one
                # job per intent, so re-importing the same file can never renew it.
                consumed = connection.execute(
                    "SELECT * FROM render_qualification_vectors WHERE vector_id=?",
                    (qualification_permit.vector_id,),
                ).fetchone()
                if consumed is not None:
                    raise RenderJobUnavailable(
                        "qualification intent was already consumed")
                connection.execute(
                    "INSERT INTO render_qualification_vectors "
                    "(vector_id,owner_scope,dispatch_key,body_sha256,job_id,consumed_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (qualification_permit.vector_id, owner_scope, hub_dispatch_key,
                     body_sha256, job_id, now),
                )
            qualification_json = _canonical(asdict(authority))
            display_json = _canonical(value["display"]) if "display" in value else None
            connection.execute(
                """INSERT INTO render_jobs (
                     id, owner_scope, dispatch_key, body_sha256, request_bytes, packet_json,
                     story_uuid, intent_id, attempt_no, gateway_attempt_key,
                     destination_site, destination_controller, destination_node, dispatch_fence,
                     profile_digest, tools_digest, compiler_digest, qualification_digest,
                     qualification_json, purpose, qualification_permit_json, display_json,
                     state, created_at, updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'accepted',?,?)""",
                (job_id, owner_scope, hub_dispatch_key, body_sha256, raw_request, packet_json,
                 dispatch["story_uuid"], dispatch["intent_id"], dispatch["attempt_no"],
                 dispatch["gateway_attempt_key"], dispatch["destination_site"],
                 dispatch["destination_controller"], dispatch["destination_node"],
                 dispatch["dispatch_fence"], authority.profile_digest, authority.tools_digest,
                 authority.compiler_digest, authority.qualification_digest,
                 qualification_json, purpose, permit_json, display_json, now, now),
            )
            row = self._owned(connection, owner_scope, job_id)
            return self._public(connection, row)

    def qualification_consumed(self, intent_id: str) -> bool:
        with self._conn() as connection:
            return connection.execute(
                "SELECT 1 FROM render_qualification_vectors WHERE vector_id=?",
                (intent_id,),
            ).fetchone() is not None

    def by_key(self, owner_scope: str, hub_dispatch_key: str) -> dict[str, Any] | None:
        owner_scope = _text(owner_scope, "owner_scope")
        hub_dispatch_key = _text(hub_dispatch_key, "dispatch_key")
        with self._conn() as connection:
            row = connection.execute(
                "SELECT * FROM render_jobs WHERE owner_scope = ? AND dispatch_key = ?",
                (owner_scope, hub_dispatch_key),
            ).fetchone()
            return self._public(connection, row) if row is not None else None

    def get(self, owner_scope: str, job_id: str) -> dict[str, Any] | None:
        owner_scope = _text(owner_scope, "owner_scope")
        _text(job_id, "job_id")
        with self._conn() as connection:
            row = connection.execute(
                "SELECT * FROM render_jobs WHERE owner_scope = ? AND id = ?",
                (owner_scope, job_id),
            ).fetchone()
            return self._public(connection, row) if row is not None else None

    def dispatchable_jobs(self) -> list[dict[str, Any]]:
        with self._conn() as connection:
            return [{
                **self._public(connection, row),
                "created_at": float(row["created_at"]),
            } for row in connection.execute(
                "SELECT * FROM render_jobs WHERE state = 'accepted' ORDER BY created_at, id"
            ).fetchall()]

    def activity_jobs(self) -> list[dict[str, Any]]:
        with self._conn() as connection:
            return [self._public(connection, row) for row in connection.execute(
                "SELECT * FROM render_jobs WHERE state NOT IN "
                "('cancelled','failed','output_ready') ORDER BY created_at, id"
            ).fetchall()]

    def terminal_jobs(self) -> list[dict[str, Any]]:
        """Failed/cancelled rows that still own an attempt workspace.

        Read-only input for the Hub's file-retirement sweep. The public projection keeps
        the workspace path private; callers derive ``runtime.root / "jobs" / id`` and
        re-read the row for leases before moving anything.
        """
        with self._conn() as connection:
            return [self._public(connection, row) for row in connection.execute(
                "SELECT * FROM render_jobs WHERE state IN ('failed','cancelled') "
                "AND workspace IS NOT NULL AND attempt_id IS NOT NULL "
                "ORDER BY updated_at, id"
            ).fetchall()]

    def claim_launch(self, owner_scope: str, job_id: str, *, machine: str,
                     dispatch_generation: int) -> dict[str, Any]:
        owner_scope = _text(owner_scope, "owner_scope")
        machine = _text(machine, "machine")
        dispatch_generation = _positive_int(dispatch_generation, "dispatch_generation")
        with self._conn() as connection:
            self._begin(connection)
            row = self._owned(connection, owner_scope, job_id)
            if row["state"] != "accepted":
                raise RenderJobConflict("render job no longer grants launch permission")
            packet = json.loads(row["packet_json"])
            authority = self._current_qualification(
                self._qualification_resolver, packet,
                allow_candidate=row["purpose"] == "qualification",
            )
            # qualification_digest already binds every authority key; the document is evidence
            # (intent_id, measured) and may differ between same-digest records. The row keeps
            # the document it was accepted with.
            if ({**asdict(authority), "document": None}
                    != {**json.loads(row["qualification_json"]), "document": None}):
                raise RenderJobUnavailable("render qualification changed after acceptance")
            required = max(
                authority.min_free_bytes,
                authority.scratch_reserve_bytes + authority.output_limit_bytes,
            )
            free = self._free_bytes(self.root)
            if isinstance(free, bool) or not isinstance(free, int) or free < required:
                raise RenderJobUnavailable("render launch lacks known reserved free disk")
            if connection.execute(
                "SELECT 1 FROM render_jobs WHERE machine = ? AND compute_held = 1", (machine,)
            ).fetchone() is not None:
                raise RenderJobUnavailable("another render already holds this machine")
            attempt_id = uuid.uuid4().hex
            launch_token = uuid.uuid4().hex
            workspace = self.root / "jobs" / row["id"] / attempt_id
            workspace.mkdir(parents=True, exist_ok=False)
            output_path = workspace / "final.mp4"
            now = time.time()
            try:
                connection.execute(
                    """UPDATE render_jobs SET state='launch_pending', machine=?,
                       dispatch_generation=?, attempt_id=?, launch_token=?, workspace=?,
                       output_path=?, compute_held=1, updated_at=?
                       WHERE id=? AND owner_scope=? AND state='accepted'""",
                    (machine, dispatch_generation, attempt_id, launch_token, str(workspace),
                     str(output_path), now, row["id"], owner_scope),
                )
            except sqlite3.IntegrityError as exc:
                raise RenderJobUnavailable("another render already holds this machine") from exc
            return {
                "job_id": row["id"], "attempt_id": attempt_id,
                "launch_token": launch_token, "machine": machine,
                "workspace": str(workspace), "output_path": str(output_path),
                "accepted_request_sha256": row["body_sha256"],
            }

    def execution_context(
        self, owner_scope: str, job_id: str, attempt_id: str, launch_token: str | None,
    ) -> RenderExecutionContext:
        owner_scope = _text(owner_scope, "owner_scope")
        with self._conn() as connection:
            row = self._owned(connection, owner_scope, job_id)
            active = (
                launch_token is not None and row["launch_token"] == launch_token
                and row["compute_held"]
                and row["state"] in {"launch_pending", "rendering", "cancel_requested",
                                     "uncertain", "stopped"}
            )
            terminal_output = launch_token is None and row["state"] == "output_ready"
            if row["attempt_id"] != attempt_id or not (active or terminal_output):
                raise RenderJobConflict("render execution context does not own this attempt")
            request_value = json.loads(row["request_bytes"])
            permit = (
                QualificationPermit(**json.loads(row["qualification_permit_json"]))
                if row["qualification_permit_json"] is not None else None
            )
            return RenderExecutionContext(
                owner_scope=row["owner_scope"], hub_job_id=row["id"],
                attempt_id=row["attempt_id"], launch_token=row["launch_token"],
                accepted_request_sha256=row["body_sha256"],
                raw_request=bytes(row["request_bytes"]), packet=json.loads(row["packet_json"]),
                assets=dict(request_value["assets"]),
                qualification=RuntimeQualification(**json.loads(row["qualification_json"])),
                qualification_permit=permit, purpose=row["purpose"],
                workspace=row["workspace"], output_path=row["output_path"],
                machine=row["machine"], dispatch_generation=row["dispatch_generation"],
            )

    def record_process_started(self, owner_scope: str, job_id: str, attempt_id: str,
                               launch_token: str, process: ProcessIdentity) -> dict[str, Any]:
        if type(process) is not ProcessIdentity:
            raise TypeError("process start requires a trusted ProcessIdentity")
        owner_scope = _text(owner_scope, "owner_scope")
        with self._conn() as connection:
            self._begin(connection)
            row = self._owned(connection, owner_scope, job_id)
            if row["attempt_id"] != attempt_id or row["launch_token"] != launch_token:
                raise RenderJobConflict("render launch ownership changed")
            same = (row["process_pid"], row["process_group_id"], row["process_start_token"]) == (
                process.pid, process.process_group_id, process.start_token)
            if row["state"] in {"rendering", "cancel_requested"} and same:
                return self._public(connection, row)
            if (row["state"] not in {"launch_pending", "cancel_requested"}
                    or row["process_pid"] is not None):
                raise RenderJobConflict("render job cannot bind another process")
            state = "cancel_requested" if row["state"] == "cancel_requested" else "rendering"
            now = time.time()
            connection.execute(
                """UPDATE render_jobs SET state=?, process_pid=?, process_group_id=?,
                   process_start_token=?, started_at=COALESCE(started_at, ?), updated_at=?
                   WHERE id=? AND owner_scope=?""",
                (state, process.pid, process.process_group_id, process.start_token,
                 now, now, job_id, owner_scope),
            )
            return self._public(connection, self._owned(connection, owner_scope, job_id))

    def record_execution_failed(
        self, owner_scope: str, job_id: str, attempt_id: str,
        launch_token: str, phase: str,
    ) -> dict[str, Any]:
        """Settle one server-known failure without accepting caller-supplied evidence."""
        if phase not in {"prelaunch_failed", "output_validation_failed"}:
            raise ValueError("render execution failure phase is unsupported")
        owner_scope = _text(owner_scope, "owner_scope")
        with self._conn() as connection:
            self._begin(connection)
            row = self._owned(connection, owner_scope, job_id)
            if row["attempt_id"] != attempt_id or row["launch_token"] != launch_token:
                raise RenderJobConflict("render failure attempt identity changed")
            if row["state"] in {"cancelled", "failed", "output_ready"}:
                return self._public(connection, row)
            prelaunch = (
                phase == "prelaunch_failed"
                and row["state"] in {"launch_pending", "cancel_requested"}
                and row["process_pid"] is None
            )
            poststop = (
                phase == "output_validation_failed"
                and row["state"] in {"stopped", "cancel_requested"}
                and row["stopped_evidence_digest"] is not None
                and row["validation_pending"] == 1
            )
            if not (prelaunch or poststop):
                raise RenderJobConflict("render failure outcome is not durably known")
            state = "cancelled" if row["cancel_requested_at"] is not None else "failed"
            connection.execute(
                "UPDATE render_jobs SET state=?, compute_held=0, validation_pending=0, "
                "recovery_note=?, updated_at=? "
                "WHERE id=? AND owner_scope=?",
                (state, phase, time.time(), job_id, owner_scope),
            )
            return self._public(
                connection, self._owned(connection, owner_scope, job_id))

    def recover_incomplete(self) -> list[dict[str, Any]]:
        """Recover without an external process check.

        A row that already carries durable stop evidence settles; every other
        interrupted row becomes honestly uncertain with compute still held.
        """
        return self._recover(None)

    def recover_incomplete_with_proof(
        self,
        process_cannot_run: Callable[[ProcessIdentity | None, float], bool],
    ) -> list[dict[str, Any]]:
        """Settle rows only when the caller proves the recorded work cannot still run.

        The callback receives the durable process identity (None when launch was recorded
        without a process) and the row's created_at. Returning true is a claim that no
        process or descendant from that attempt can still be running. A callback that
        cannot prove this leaves the row honestly uncertain and compute held.
        """
        if not callable(process_cannot_run):
            raise TypeError("recovery proof must be callable")
        return self._recover(process_cannot_run)

    def _recover(
        self,
        process_cannot_run: Callable[[ProcessIdentity | None, float], bool] | None,
    ) -> list[dict[str, Any]]:
        with self._conn() as connection:
            self._begin(connection)
            rows = connection.execute(
                "SELECT * FROM render_jobs WHERE compute_held=1 AND state IN "
                "('launch_pending','rendering','cancel_requested','stopped','uncertain') "
                "ORDER BY created_at, id"
            ).fetchall()
            now = time.time()
            for row in rows:
                identity = self._row_identity(row)
                proved = row["stopped_evidence_digest"] is not None
                if proved:
                    note = (
                        "recovered: durable stop evidence proves the recorded process "
                        "was stopped")
                elif process_cannot_run is not None:
                    try:
                        created_at = float(row["created_at"])
                    except (TypeError, ValueError):
                        created_at = -1.0
                    try:
                        proved = bool(process_cannot_run(identity, created_at))
                    except Exception:
                        proved = False
                    note = (
                        "recovered: recorded process cannot still run" if proved else
                        "Hub restarted with launch or process outcome unproved")
                else:
                    note = "Hub restarted with launch or process outcome unproved"
                if proved:
                    state = (
                        "cancelled" if row["cancel_requested_at"] is not None else "failed")
                    connection.execute(
                        "UPDATE render_jobs SET state=?, compute_held=0, "
                        "validation_pending=0, recovery_note=?, updated_at=? "
                        "WHERE id=? AND owner_scope=? AND compute_held=1",
                        (state, note, now, row["id"], row["owner_scope"]),
                    )
                else:
                    connection.execute(
                        "UPDATE render_jobs SET state='uncertain', recovery_note=?, "
                        "updated_at=? WHERE id=? AND owner_scope=? AND compute_held=1",
                        (note, now, row["id"], row["owner_scope"]),
                    )
            uncertain = connection.execute(
                "SELECT * FROM render_jobs WHERE state='uncertain' AND compute_held=1 "
                "ORDER BY created_at, id"
            ).fetchall()
            return [self._public(connection, row) for row in uncertain]

    def recovery_record(self, owner_scope: str, job_id: str) -> dict[str, Any] | None:
        """Internal recovery view: the durable process identity is never published."""
        owner_scope = _text(owner_scope, "owner_scope")
        _text(job_id, "job_id")
        with self._conn() as connection:
            row = connection.execute(
                "SELECT * FROM render_jobs WHERE id = ? AND owner_scope = ?",
                (job_id, owner_scope),
            ).fetchone()
            if row is None:
                return None
            return {
                "id": row["id"], "owner_scope": row["owner_scope"],
                "machine": row["machine"], "state": row["state"],
                "attempt_id": row["attempt_id"],
                "compute_held": bool(row["compute_held"]),
                "cancel_requested_at": row["cancel_requested_at"],
                "stopped_evidence_digest": row["stopped_evidence_digest"],
                "created_at": float(row["created_at"]),
                "process": self._row_identity(row),
            }

    def settle_recovered(
        self, owner_scope: str, job_id: str, *, proof: str,
    ) -> dict[str, Any]:
        """Terminal settlement after the adapter has proved the attempt cannot still run.

        This is the internal counterpart to ``record_process_stopped`` for a row whose
        process handle did not survive the restart. It never accepts caller evidence and
        never runs without proof supplied by the trusted adapter.
        """
        proof = _text(proof, "proof", maximum=512)
        owner_scope = _text(owner_scope, "owner_scope")
        with self._conn() as connection:
            self._begin(connection)
            row = self._owned(connection, owner_scope, job_id)
            if row["state"] in {"cancelled", "failed", "output_ready"}:
                return self._public(connection, row)
            if not row["compute_held"]:
                raise RenderJobConflict("render job does not hold durable compute")
            if row["state"] not in {
                    "launch_pending", "rendering", "cancel_requested", "uncertain", "stopped"}:
                raise RenderJobConflict("render job cannot settle from its current state")
            state = (
                "cancelled" if row["cancel_requested_at"] is not None else "failed")
            connection.execute(
                "UPDATE render_jobs SET state=?, compute_held=0, validation_pending=0, "
                "recovery_note=?, updated_at=? WHERE id=? AND owner_scope=?",
                (state, proof, time.time(), job_id, owner_scope),
            )
            return self._public(connection, self._owned(connection, owner_scope, job_id))

    def request_cancel_unsettled(self, owner_scope: str, job_id: str) -> dict[str, Any]:
        """Record cancellation without claiming the still-unknown process outcome.

        The state stays ``uncertain`` (or the launch state) until a later reconciliation
        proves the exact recorded process group is gone; only then does compute release.
        """
        owner_scope = _text(owner_scope, "owner_scope")
        with self._conn() as connection:
            self._begin(connection)
            row = self._owned(connection, owner_scope, job_id)
            if row["state"] in {"cancelled", "failed", "output_ready"}:
                return self._public(connection, row)
            if row["state"] not in {
                    "launch_pending", "rendering", "cancel_requested", "uncertain", "stopped"}:
                raise RenderJobConflict("render job has no unsettled cancellation target")
            now = time.time()
            connection.execute(
                "UPDATE render_jobs SET cancel_requested_at=COALESCE(cancel_requested_at,?), "
                "recovery_note='cancellation requested; process outcome remains unproved', "
                "updated_at=? WHERE id=? AND owner_scope=?",
                (now, now, job_id, owner_scope),
            )
            return self._public(
                connection, self._owned(connection, owner_scope, job_id))

    def request_cancel(self, owner_scope: str, job_id: str) -> dict[str, Any]:
        owner_scope = _text(owner_scope, "owner_scope")
        with self._conn() as connection:
            self._begin(connection)
            row = self._owned(connection, owner_scope, job_id)
            return self._cancel_row(connection, row)

    def request_cancel_pending(self, owner_scope: str, job_id: str) -> dict[str, Any]:
        """Persist cancellation while the trusted adapter still owns a live process group."""
        owner_scope = _text(owner_scope, "owner_scope")
        with self._conn() as connection:
            self._begin(connection)
            row = self._owned(connection, owner_scope, job_id)
            if row["state"] in {"cancelled", "failed", "output_ready"}:
                return self._public(connection, row)
            if row["state"] not in {
                    "launch_pending", "rendering", "cancel_requested", "uncertain", "stopped"}:
                raise RenderJobConflict("render job has no active adapter-owned work")
            now = time.time()
            connection.execute(
                "UPDATE render_jobs SET state='cancel_requested', "
                "cancel_requested_at=COALESCE(cancel_requested_at,?), updated_at=? "
                "WHERE id=? AND owner_scope=?",
                (now, now, job_id, owner_scope),
            )
            return self._public(
                connection, self._owned(connection, owner_scope, job_id))

    @classmethod
    def _cancel_row(
        cls, connection: sqlite3.Connection, row: sqlite3.Row,
    ) -> dict[str, Any]:
        if row["state"] in {"cancelled", "output_ready", "failed"}:
            return cls._public(connection, row)
        settled = (
            row["state"] == "accepted"
            or (row["state"] == "stopped" and row["validation_pending"] == 0)
        )
        state = "cancelled" if settled else "cancel_requested"
        compute_held = 0 if settled else row["compute_held"]
        now = time.time()
        connection.execute(
            "UPDATE render_jobs SET state=?, compute_held=?, "
            "cancel_requested_at=COALESCE(cancel_requested_at,?), updated_at=? "
            "WHERE id=? AND owner_scope=?",
            (state, compute_held, now, now, row["id"], row["owner_scope"]),
        )
        return cls._public(
            connection, cls._owned(connection, row["owner_scope"], row["id"]))

    def request_cancel_by_key(
        self, owner_scope: str, hub_dispatch_key: str, expected_request_sha256: str,
    ) -> dict[str, Any] | CancelledBeforeAcceptanceReceipt:
        owner_scope = _text(owner_scope, "owner_scope")
        hub_dispatch_key = _text(hub_dispatch_key, "dispatch_key")
        expected_request_sha256 = _digest(
            expected_request_sha256, "expected_request_sha256")
        with self._conn() as connection:
            self._begin(connection)
            row = connection.execute(
                "SELECT * FROM render_jobs WHERE owner_scope = ? AND dispatch_key = ?",
                (owner_scope, hub_dispatch_key),
            ).fetchone()
            if row is not None:
                if row["body_sha256"] != expected_request_sha256:
                    raise RenderJobConflict(
                        "render dispatch key belongs to a different exact body")
                return self._cancel_row(connection, row)
            fence = connection.execute(
                "SELECT * FROM render_cancel_fences WHERE owner_scope = ? AND dispatch_key = ?",
                (owner_scope, hub_dispatch_key),
            ).fetchone()
            if fence is None:
                connection.execute(
                    "INSERT INTO render_cancel_fences "
                    "(owner_scope, dispatch_key, expected_request_sha256, fence_id, cancelled_at) "
                    "VALUES (?,?,?,?,?)",
                    (owner_scope, hub_dispatch_key, expected_request_sha256,
                     uuid.uuid4().hex, time.time()),
                )
                fence = connection.execute(
                    "SELECT * FROM render_cancel_fences "
                    "WHERE owner_scope = ? AND dispatch_key = ?",
                    (owner_scope, hub_dispatch_key),
                ).fetchone()
            elif fence["expected_request_sha256"] != expected_request_sha256:
                raise RenderJobConflict(
                    "render dispatch key was cancelled for a different exact body")
            return CancelledBeforeAcceptanceReceipt(
                owner_scope=fence["owner_scope"], dispatch_key=fence["dispatch_key"],
                expected_request_sha256=fence["expected_request_sha256"],
                fence_id=fence["fence_id"], cancelled_at=float(fence["cancelled_at"]),
            )

    def record_process_stopped(
        self, owner_scope: str, job_id: str, attempt_id: str,
        evidence: StoppedProcessEvidence, *, validation_pending: bool = False,
    ) -> dict[str, Any]:
        if type(evidence) is not StoppedProcessEvidence:
            raise TypeError("process stop requires trusted StoppedProcessEvidence")
        if type(validation_pending) is not bool:
            raise TypeError("validation_pending must be a trusted adapter boolean")
        owner_scope = _text(owner_scope, "owner_scope")
        evidence_json = _canonical({
            "process": asdict(evidence.process), "observed_at": float(evidence.observed_at),
            "exit_code": evidence.exit_code, "reason": evidence.reason,
        })
        evidence_digest = hashlib.sha256(evidence_json.encode()).hexdigest()
        with self._conn() as connection:
            self._begin(connection)
            row = self._owned(connection, owner_scope, job_id)
            if row["attempt_id"] != attempt_id:
                raise RenderJobConflict("render attempt identity changed")
            stored = (row["process_pid"], row["process_group_id"], row["process_start_token"])
            exact = (evidence.process.pid, evidence.process.process_group_id,
                     evidence.process.start_token)
            if stored != exact:
                raise RenderJobConflict("stop evidence does not identify the owned process")
            if row["stopped_evidence_digest"] is not None:
                if row["stopped_evidence_digest"] != evidence_digest:
                    raise RenderJobConflict("render stop evidence changed")
                if bool(row["validation_pending"]) != validation_pending:
                    raise RenderJobConflict("render validation activity changed")
                return self._public(connection, row)
            if row["state"] not in {"rendering", "cancel_requested", "uncertain"}:
                raise RenderJobConflict("render job cannot record process stop")
            completed = evidence.reason == "completed" and evidence.exit_code == 0
            cancelled = row["cancel_requested_at"] is not None
            if completed and not cancelled:
                state, compute_held = "stopped", 1
            else:
                state = "cancelled" if cancelled else "failed"
                compute_held = 0
                validation_pending = False
            connection.execute(
                """UPDATE render_jobs SET state=?, compute_held=?, stopped_evidence_json=?,
                   stopped_evidence_digest=?, validation_pending=?, updated_at=?
                   WHERE id=? AND owner_scope=?""",
                (state, compute_held, evidence_json, evidence_digest,
                 int(validation_pending), time.time(),
                 job_id, owner_scope),
            )
            return self._public(connection, self._owned(connection, owner_scope, job_id))

    def publish_output(self, owner_scope: str, job_id: str,
                       receipt: ValidatedOutputReceipt) -> dict[str, Any]:
        if type(receipt) is not ValidatedOutputReceipt:
            raise TypeError("output publication requires a trusted validated receipt")
        owner_scope = _text(owner_scope, "owner_scope")
        receipt_json = _canonical({**asdict(receipt), "feature_losses": list(receipt.feature_losses)})
        with self._conn() as connection:
            self._begin(connection)
            row = self._owned(connection, owner_scope, job_id)
            if row["output_receipt_digest"] is not None:
                if (row["output_receipt_digest"] != receipt.receipt_digest
                        or row["output_receipt_json"] != receipt_json):
                    raise RenderJobConflict("render output receipt changed")
                return self._public(connection, row)
            if row["state"] != "stopped":
                raise RenderJobConflict("render output cannot be published before exact process stop")
            expected = (
                row["owner_scope"], row["id"], row["attempt_id"], row["body_sha256"],
                row["profile_digest"], row["tools_digest"], row["compiler_digest"],
                row["qualification_digest"],
            )
            actual = (
                receipt.owner_scope, receipt.hub_job_id, receipt.attempt_id,
                receipt.accepted_request_sha256, receipt.profile_digest,
                receipt.tools_digest, receipt.compiler_digest,
                receipt.qualification_digest,
            )
            if actual != expected:
                raise RenderJobConflict("render output receipt authority changed")
            connection.execute(
                """UPDATE render_jobs SET state='output_ready', compute_held=0,
                   validation_pending=0,
                   output_receipt_json=?, output_receipt_digest=?, updated_at=?
                   WHERE id=? AND owner_scope=?""",
                (receipt_json, receipt.receipt_digest, time.time(), job_id, owner_scope),
            )
            return self._public(connection, self._owned(connection, owner_scope, job_id))

    def acquire_file_lease(self, owner_scope: str, job_id: str,
                           lease_kind: str, lease_id: str) -> dict[str, Any]:
        owner_scope = _text(owner_scope, "owner_scope")
        if lease_kind not in _FILE_LEASE_KINDS:
            raise ValueError("unsupported render file lease kind")
        lease_id = _text(lease_id, "lease_id", maximum=512)
        with self._conn() as connection:
            self._begin(connection)
            self._owned(connection, owner_scope, job_id)
            connection.execute(
                """INSERT INTO render_file_leases(job_id, lease_kind, lease_id, active, updated_at)
                   VALUES(?,?,?,1,?) ON CONFLICT(job_id,lease_kind,lease_id)
                   DO UPDATE SET active=1, updated_at=excluded.updated_at""",
                (job_id, lease_kind, lease_id, time.time()),
            )
            return self._public(connection, self._owned(connection, owner_scope, job_id))

    def release_file_lease(self, owner_scope: str, job_id: str,
                           lease_kind: str, lease_id: str) -> dict[str, Any]:
        owner_scope = _text(owner_scope, "owner_scope")
        if lease_kind not in _FILE_LEASE_KINDS:
            raise ValueError("unsupported render file lease kind")
        lease_id = _text(lease_id, "lease_id", maximum=512)
        with self._conn() as connection:
            self._begin(connection)
            self._owned(connection, owner_scope, job_id)
            connection.execute(
                """UPDATE render_file_leases SET active=0, updated_at=?
                   WHERE job_id=? AND lease_kind=? AND lease_id=?""",
                (time.time(), job_id, lease_kind, lease_id),
            )
            return self._public(connection, self._owned(connection, owner_scope, job_id))
