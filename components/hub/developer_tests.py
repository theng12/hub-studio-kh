"""Node-side Developer Mode: the persisted OFF switch and the closed test ledger.

The normative contract is ``studio.developer-test.v1`` (candidate 2). This module owns what a
*node* may answer: its own setting, capabilities, and the exact-byte idempotent attempt ledger.
The lifecycle between acceptance and a terminal receipt belongs to ``developer_test_service``;
this module delegates to it lazily so the setting and ledger readers stay importable without a
runner. Nothing here may report ``supported=true`` until bounded release proof exists.

Only the existing Fleet/controller transport is used. A controller relays the same fixed routes
to the node's own ``local`` path; a node credential cannot authorise a switch or a test.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import secrets
import threading
import time
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import Response

from .standalone_config import _atomic_write, data_dir

RUNNER_ID = "studio.image-generation-test.v1"
SETTING_FORMAT = "studio.developer-test.node-setting.v1"
SETTING_RESULT_FORMAT = "studio.developer-test.node-setting-result.v1"
STATUS_FORMAT = "studio.developer-test.status.v1"
LOG_FORMAT = "studio.developer-test.log-chunk.v1"
ERROR_FORMAT = "studio.developer-test.error.v1"
REQUEST_FORMAT = "studio.developer-test.request.v1"
CANCEL_FORMAT = "studio.developer-test.cancel.v1"

SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
MAX_INT = 9007199254740991
REQUEST_BYTES_CEILING = 2 * 1024 * 1024
RESPONSE_BYTES_CEILING = 4 * 1024 * 1024
LOG_CHUNK_CEILING = 1024 * 1024
MAX_REFERENCES = 4096

KINDS = ("reference_counts", "prompt_length", "low_ram_mode", "memory_pressure", "new_model")
TERMINAL_STATES = ("passed", "failed", "cancelled", "interrupted", "not_run")

_lock = threading.RLock()


class Refused(Exception):
    """One refusal in the contract's error shape."""

    def __init__(self, status: int, code: str, detail: str, *, retryable: bool = False,
                 acceptance: str = "not_accepted", key: str | None = None):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail[:4096]
        self.retryable = retryable
        self.acceptance = acceptance
        self.key = key


def _root():
    try:
        return data_dir() / "developer-tests"
    except (OSError, ValueError):
        return None


def _setting_path():
    root = _root()
    return None if root is None else root / "setting.json"


def _ledger_path():
    root = _root()
    return None if root is None else root / "ledger.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _revision() -> str:
    return "rev-" + secrets.token_hex(16)


def _identities() -> tuple[str, str | None]:
    """This Mac's stable node id and the controller id it answers to.

    A Mac with no parent is its own controller, so both ids are genuinely the same. A leaf has
    only the parent's address persisted; it does not persist the controller's roster identity, so
    the controller id is **unknown** rather than guessed from a hostname or label. Callers refuse
    an unverified identity; they never invent one.
    """
    from .backend import control_plane, fleet_nodes
    node = str(fleet_nodes.this_machine().get("id") or "local")
    settings = control_plane.load_settings()
    parent = str(settings.get("parent_controller_url") or "").strip()
    if not parent:
        return node, node
    # Only the controller's own roster id, recorded with the adopted credential. A missing id is
    # unverified; an address or label is never promoted into an identity.
    controller = str(settings.get("parent_controller_id") or "").strip()
    return node, controller or None


def _require_identity() -> tuple[str, str]:
    node, controller = _identities()
    if controller is None:
        raise Refused(503, "release_unknown",
                      "this Mac's controller identity is not persisted, so Developer Mode "
                      "cannot report a verified node/controller identity")
    return node, controller


def _reason(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail[:4096]}


def _error_body(code: str, detail: str, *, retryable: bool = False,
                acceptance: str = "not_accepted", key: str | None = None) -> bytes:
    try:
        node, controller = _identities()
    except Exception:  # noqa: BLE001 - a refusal must still be renderable
        node, controller = None, None
    verified = controller is not None
    return json.dumps({
        "format": ERROR_FORMAT,
        "node_id": node if verified and isinstance(node, str)
        and SAFE_SEGMENT.fullmatch(node) else None,
        "controller_id": controller if verified and isinstance(controller, str)
        and SAFE_SEGMENT.fullmatch(controller) else None,
        "idempotency_key": key if key and SAFE_SEGMENT.fullmatch(key) else None,
        "reason": _reason(code, detail),
        "retryable": bool(retryable),
        "acceptance": acceptance,
    }, separators=(",", ":")).encode()


def _refusal(exc: Refused, key: str | None = None) -> tuple[int, bytes]:
    return exc.status, _error_body(exc.code, exc.detail, retryable=exc.retryable,
                                   acceptance=exc.acceptance, key=exc.key or key)


# ---------------------------------------------------------------------------
# Raw JSON: bounded, UTF-8, duplicate keys and non-finite numbers refused.


def _no_duplicates(pairs):
    seen = set()
    for name, _ in pairs:
        if name in seen:
            raise ValueError(f"duplicate JSON key {name!r}")
        seen.add(name)
    return dict(pairs)


def _reject_constant(value: str):
    raise ValueError(f"non-finite JSON number {value}")


def parse_json(raw: bytes) -> Any:
    if len(raw) > REQUEST_BYTES_CEILING:
        raise Refused(413, "invalid_request", "request body is over the wire ceiling")
    try:
        text = raw.decode("utf-8")
        return json.loads(text, object_pairs_hook=_no_duplicates,
                          parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        raise Refused(400, "invalid_request", f"body is not strict JSON: {exc}") from exc


def _object(value: object, name: str, keys: tuple[str, ...]) -> dict:
    if not isinstance(value, dict):
        raise Refused(400, "invalid_request", f"{name} must be an object")
    missing = [key for key in keys if key not in value]
    unknown = sorted(set(value) - set(keys))
    if missing:
        raise Refused(400, "invalid_request", f"{name} is missing {missing[0]!r}")
    if unknown:
        raise Refused(400, "invalid_request", f"{name} has unknown field {unknown[0]!r}")
    return value


def _string(value: object, name: str, *, pattern: re.Pattern | None = None,
            minimum: int = 1, maximum: int = 4096) -> str:
    if not isinstance(value, str) or len(value) < minimum or len(value) > maximum:
        raise Refused(400, "invalid_request",
                      f"{name} must be a string of {minimum}..{maximum} characters")
    if pattern is not None and not pattern.fullmatch(value):
        raise Refused(400, "invalid_request", f"{name} has an invalid shape")
    return value


def _integer(value: object, name: str, *, minimum: int = 0,
             maximum: int = MAX_INT) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise Refused(400, "invalid_request", f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise Refused(400, "invalid_request", f"{name} is outside its bounds")
    return value


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Refused(400, "invalid_request", f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > MAX_INT:
        raise Refused(400, "invalid_request", f"{name} is outside its bounds")
    return number


def _const(value: object, expected: str, name: str) -> None:
    if value != expected:
        raise Refused(400, "invalid_request", f"{name} must be {expected!r}")


def validate_request(value: object) -> dict:
    """The closed request object, exactly the schema's required keys and bounds."""
    row = _object(value, "request", (
        "format", "run_id", "test_id", "runner_id", "model", "kind", "settings",
        "input_refs", "requested_limits", "runner_revision", "runner_sha256",
        "fixture_set_sha256", "node_setting_revision"))
    _const(row["format"], REQUEST_FORMAT, "format")
    for name in ("run_id", "test_id"):
        _string(row[name], name, pattern=SAFE_SEGMENT)
    _const(row["runner_id"], RUNNER_ID, "runner_id")
    model = _object(row["model"], "model", ("id", "revision"))
    _string(model["id"], "model.id", maximum=256)
    _string(model["revision"], "model.revision", maximum=256)
    if row["kind"] not in KINDS:
        raise Refused(400, "invalid_request", "kind is not a supported test kind")
    settings = _object(row["settings"], "settings", (
        "width_px", "height_px", "steps", "guidance", "seed", "memory_mode",
        "reference_count", "prompt_characters", "fixture_set_id", "prompt_sha256"))
    for name in ("width_px", "height_px", "steps"):
        _integer(settings[name], f"settings.{name}", minimum=1)
    if settings["guidance"] is not None:
        _number(settings["guidance"], "settings.guidance")
    _integer(settings["seed"], "settings.seed")
    if settings["memory_mode"] not in ("default", "bounded-staged"):
        raise Refused(400, "invalid_request", "settings.memory_mode is not supported")
    count = _integer(settings["reference_count"], "settings.reference_count",
                     maximum=MAX_REFERENCES)
    _integer(settings["prompt_characters"], "settings.prompt_characters")
    _string(settings["fixture_set_id"], "settings.fixture_set_id", pattern=SAFE_SEGMENT)
    _string(settings["prompt_sha256"], "settings.prompt_sha256", pattern=SHA256_HEX)
    refs = row["input_refs"]
    if not isinstance(refs, list) or len(refs) > MAX_REFERENCES:
        raise Refused(400, "invalid_request", "input_refs must be a bounded array")
    if len(refs) != count:
        raise Refused(400, "invalid_request",
                      "input_refs length must equal settings.reference_count")
    for index, ref in enumerate(refs):
        item = _object(ref, f"input_refs[{index}]", ("fixture_id", "sha256", "size_bytes"))
        _string(item["fixture_id"], f"input_refs[{index}].fixture_id", pattern=SAFE_SEGMENT)
        _string(item["sha256"], f"input_refs[{index}].sha256", pattern=SHA256_HEX)
        _integer(item["size_bytes"], f"input_refs[{index}].size_bytes", minimum=1)
    limits = _object(row["requested_limits"], "requested_limits", (
        "max_runtime_seconds", "max_process_tree_rss_bytes",
        "max_system_swap_growth_bytes", "test_start_min_free_bytes"))
    _integer(limits["max_runtime_seconds"], "requested_limits.max_runtime_seconds", minimum=1)
    _integer(limits["max_process_tree_rss_bytes"],
             "requested_limits.max_process_tree_rss_bytes", minimum=1)
    _integer(limits["max_system_swap_growth_bytes"],
             "requested_limits.max_system_swap_growth_bytes")
    _integer(limits["test_start_min_free_bytes"],
             "requested_limits.test_start_min_free_bytes")
    _string(row["runner_revision"], "runner_revision", maximum=256)
    _string(row["runner_sha256"], "runner_sha256", pattern=SHA256_HEX)
    _string(row["fixture_set_sha256"], "fixture_set_sha256", pattern=SHA256_HEX)
    _string(row["node_setting_revision"], "node_setting_revision", pattern=SAFE_SEGMENT)
    return row


def validate_setting_request(value: object) -> dict:
    row = _object(value, "settingRequest",
                  ("format", "enabled", "expected_revision", "request_id"))
    _const(row["format"], SETTING_FORMAT, "format")
    if not isinstance(row["enabled"], bool):
        raise Refused(400, "invalid_request", "enabled must be true or false")
    _string(row["expected_revision"], "expected_revision", pattern=SAFE_SEGMENT)
    _string(row["request_id"], "request_id", pattern=SAFE_SEGMENT)
    return row


def validate_cancel_request(value: object) -> dict:
    row = _object(value, "cancelRequest", ("format", "request_id"))
    _const(row["format"], CANCEL_FORMAT, "format")
    _string(row["request_id"], "request_id", pattern=SAFE_SEGMENT)
    return row


# ---------------------------------------------------------------------------
# Setting: persisted, revisioned, exact-byte idempotent, OFF by default.


def _setting_error() -> Refused:
    return Refused(503, "integrity_error",
                   "the persisted Developer Mode setting could not be read or written")


def _valid_setting_store(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("format") != SETTING_FORMAT:
        return False
    if not isinstance(value.get("enabled"), bool) or not isinstance(value.get("verified"), bool):
        return False
    if not isinstance(value.get("revision"), str) or not SAFE_SEGMENT.fullmatch(value["revision"]):
        return False
    if value.get("updated_at") is not None and not isinstance(value["updated_at"], str):
        return False
    if value.get("request_id") is not None and (
            not isinstance(value["request_id"], str)
            or not SAFE_SEGMENT.fullmatch(value["request_id"])):
        return False
    requests = value.get("requests")
    if not isinstance(requests, dict):
        return False
    for request_id, entry in requests.items():
        if not SAFE_SEGMENT.fullmatch(str(request_id)) or not isinstance(entry, dict):
            return False
        if not isinstance(entry.get("sha256"), str) or not SHA256_HEX.fullmatch(entry["sha256"]):
            return False
        if not isinstance(entry.get("result"), dict):
            return False
    return True


def ensure_initial_setting() -> bool:
    """Create the persisted verified OFF exactly once, at Hub start.

    Absence after that means the file was removed, so a GET reports unverified rather than
    silently minting a new authority. A malformed file is never overwritten.
    """
    with _lock:
        path = _setting_path()
        if path is None:
            return False
        if path.exists():
            return True
        record = {
            "format": SETTING_FORMAT,
            "enabled": False,
            "revision": _revision(),
            "verified": True,
            "updated_at": _now(),
            "request_id": None,
            "requests": {},
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, record)
            return True
        except OSError:
            return False


def _load_setting() -> tuple[dict | None, Refused | None]:
    path = _setting_path()
    if path is None or not path.exists():
        return None, _setting_error()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not _valid_setting_store(value):
            return None, _setting_error()
        return value, None
    except (OSError, ValueError, TypeError):
        return None, _setting_error()


def _setting_result(record: dict) -> dict:
    node, controller = _require_identity()
    return {
        "format": SETTING_RESULT_FORMAT,
        "node_id": node,
        "controller_id": controller,
        "enabled": bool(record["enabled"]),
        "revision": record["revision"],
        "verified": bool(record["verified"]),
        "reason": None,
        "updated_at": record.get("updated_at"),
        "request_id": record.get("request_id"),
    }


def setting_state() -> tuple[int, bytes]:
    """GET /setting. Absent state becomes a verified OFF with a real revision."""
    _require_identity()
    with _lock:
        record, error = _load_setting()
    if error is not None:
        node, controller = _require_identity()
        result = {
            "format": SETTING_RESULT_FORMAT,
            "node_id": node,
            "controller_id": controller,
            "enabled": False,
            "revision": "unavailable",
            "verified": False,
            "reason": _reason("integrity_error",
                              "the persisted Developer Mode setting could not be read"),
            "updated_at": None,
            "request_id": None,
        }
        return 200, json.dumps(result, separators=(",", ":")).encode()
    return 200, json.dumps(_setting_result(record), separators=(",", ":")).encode()


def change_setting(raw: bytes) -> tuple[int, bytes]:
    """POST /setting: expected revision, exact-byte request id replay, persist before success."""
    _require_identity()
    value = validate_setting_request(parse_json(raw))
    digest = _sha256(raw)
    with _lock:
        record, error = _load_setting()
        if error is not None:
            raise error
        seen = record["requests"].get(value["request_id"])
        if seen is not None:
            if seen["sha256"] == digest:
                return 200, json.dumps(seen["result"], separators=(",", ":")).encode()
            raise Refused(409, "conflict",
                          "request_id was already used for different exact bytes")
        if value["expected_revision"] != record["revision"]:
            raise Refused(409, "stale_setting",
                          "expected_revision does not match the persisted revision")
        record["enabled"] = bool(value["enabled"])
        record["revision"] = _revision()
        record["updated_at"] = _now()
        record["request_id"] = value["request_id"]
        record["verified"] = True
        result = _setting_result(record)
        record["requests"][value["request_id"]] = {"sha256": digest, "result": result}
        try:
            path = _setting_path()
            if path is None:
                raise _setting_error()
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, record)
            readback, error = _load_setting()
        except (OSError, ValueError):
            raise _setting_error() from None
        if (error is not None or readback is None
                or readback.get("revision") != record["revision"]
                or bool(readback.get("enabled")) != record["enabled"]):
            raise Refused(503, "integrity_error",
                          "the setting was written but did not read back as written")
        return 200, json.dumps(result, separators=(",", ":")).encode()


# ---------------------------------------------------------------------------
# Attempt ledger: closed, atomic, keyed by exact request bytes.


def _load_ledger() -> dict:
    path = _ledger_path()
    if path is None:
        raise _setting_error()
    try:
        if not path.exists():
            return {"format": "studio.developer-test.ledger.v1", "attempts": {}}
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict)
                or value.get("format") != "studio.developer-test.ledger.v1"
                or not isinstance(value.get("attempts"), dict)):
            raise ValueError("invalid ledger")
        return value
    except (OSError, ValueError) as exc:
        raise Refused(503, "integrity_error",
                      "the Developer Mode ledger could not be read") from exc


def _write_ledger(value: dict) -> None:
    path = _ledger_path()
    if path is None:
        raise _setting_error()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, value)


def _status_of(row: dict) -> dict:
    node, controller = _require_identity()
    return {
        "format": STATUS_FORMAT,
        "node_id": node,
        "controller_id": controller,
        "idempotency_key": row["idempotency_key"],
        "run_id": row["run_id"],
        "test_id": row["test_id"],
        "attempt_id": row["attempt_id"],
        "request_sha256": row["request_sha256"],
        "accepted": bool(row["accepted"]),
        "state": row["state"],
        "reason": row.get("reason"),
        "cancel_requested": bool(row.get("cancel_requested")),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "receipt_sha256": row.get("receipt_sha256"),
        "resource_release_confirmed": bool(row.get("resource_release_confirmed")),
    }


def accept_attempt(key: str, raw: bytes, request: dict, setting_revision: str, *,
                   actor: dict, destination: str, receipt_context: dict | None = None) -> dict:
    """Durably accept one attempt, atomically, before any workload starts.

    A repeat of the same key with the same exact bytes and the same authenticated
    scope/destination returns the original attempt and never launches again; different bytes or a
    different actor/destination conflict. These bindings stay internal; they add no public field.
    The runner calls this only after it has proved its packaged identity and admission; until then
    `submit` refuses before reaching here.
    """
    _require_identity()
    scope = f"{actor.get('kind') or ''}:{actor.get('node_id') or ''}"
    digest = _sha256(raw)
    with _lock:
        ledger = _load_ledger()
        existing = ledger["attempts"].get(key)
        if existing is not None:
            if (existing.get("request_sha256") != digest
                    or existing.get("actor_scope") != scope
                    or existing.get("destination") != destination):
                raise Refused(409, "conflict",
                              "idempotency key was already used for a different attempt identity")
            return existing
        attempt_id = "att-" + secrets.token_hex(12)
        now = _now()
        row = {
            "idempotency_key": key,
            "request_sha256": digest,
            "request_base64": base64.b64encode(raw).decode("ascii"),
            "attempt_id": attempt_id,
            "run_id": request["run_id"],
            "test_id": request["test_id"],
            "state": "waiting_idle",
            "accepted": True,
            "reason": _reason("production_busy", "waiting for production to be idle"),
            "cancel_requested": False,
            "created_at": now,
            "updated_at": now,
            "node_setting_revision": setting_revision,
            "actor_scope": scope,
            "destination": destination,
            "receipt_sha256": None,
            "resource_release_confirmed": False,
            "receipt_context": receipt_context,
        }
        ledger["attempts"][key] = row
        _write_ledger(ledger)
        return row


def attempt(key: str) -> dict | None:
    if not SAFE_SEGMENT.fullmatch(str(key or "")):
        raise Refused(400, "invalid_request", "key is not a safe segment")
    with _lock:
        return _load_ledger()["attempts"].get(key)


def update_attempt(key: str, **fields) -> dict | None:
    """Project a non-terminal change (installing/running, install ownership, cancellation).

    A terminal row is never rewritten: its receipt and log bytes are immutable at settlement.
    """
    with _lock:
        ledger = _load_ledger()
        row = ledger["attempts"].get(key)
        if row is None or row["state"] in TERMINAL_STATES:
            return row
        row.update(fields)
        row["updated_at"] = _now()
        _write_ledger(ledger)
        return row


def settle_attempt(key: str, **fields) -> dict | None:
    """Write the one terminal settlement for this attempt, including immutable receipt/log bytes."""
    with _lock:
        ledger = _load_ledger()
        row = ledger["attempts"].get(key)
        if row is None or row["state"] in TERMINAL_STATES:
            return row
        row.update(fields)
        row["updated_at"] = _now()
        _write_ledger(ledger)
        return row


def cancel_attempt(key: str, raw: bytes) -> tuple[int, bytes]:
    validate_cancel_request(parse_json(raw))
    with _lock:
        ledger = _load_ledger()
        row = ledger["attempts"].get(key)
        if row is None:
            raise Refused(404, "unknown_key", "no attempt exists for this key", key=key)
        if row["state"] in TERMINAL_STATES:
            return 200, json.dumps(_status_of(row), separators=(",", ":")).encode()
        row["cancel_requested"] = True
        row["reason"] = _reason("cancel_requested", "cancellation requested")
        row["updated_at"] = _now()
        _write_ledger(ledger)
        attempt_id = row["attempt_id"]
    # Outside the ledger lock: the service takes its own lock and may settle a waiting attempt.
    from . import developer_test_service
    developer_test_service.request_cancel(attempt_id, key=key)
    with _lock:
        row = _load_ledger()["attempts"].get(key) or row
    return 202, json.dumps(_status_of(row), separators=(",", ":")).encode()


def attempt_status(key: str) -> tuple[int, bytes]:
    row = attempt(key)
    if row is None:
        raise Refused(404, "unknown_key", "no attempt exists for this key", key=key)
    return 200, json.dumps(_status_of(row), separators=(",", ":")).encode()


def attempt_receipt(key: str) -> tuple[int, bytes]:
    row = attempt(key)
    if row is None:
        raise Refused(404, "unknown_key", "no attempt exists for this key", key=key)
    raw = row.get("receipt_base64")
    if not raw:
        raise Refused(409, "incomplete_evidence",
                      "this attempt has no settled receipt yet", key=key)
    return 200, base64.b64decode(raw)


def attempt_log(key: str, offset: int, length: int) -> tuple[int, bytes]:
    _require_identity()
    row = attempt(key)
    if row is None:
        raise Refused(404, "unknown_key", "no attempt exists for this key", key=key)
    raw = base64.b64decode(row.get("log_base64") or "")
    if "log_base64" not in row:
        raise Refused(409, "incomplete_evidence",
                      "this attempt has no retained log yet", key=key)
    if offset > len(raw):
        raise Refused(400, "invalid_request", "offset_bytes is past the end of the log")
    actual = min(length, len(raw) - offset)
    if actual > LOG_CHUNK_CEILING:
        raise Refused(413, "invalid_request", "the decoded log chunk is over the chunk ceiling")
    chunk = raw[offset:offset + actual]
    node, controller = _identities()
    descriptor = {
        "sha256": row.get("log_sha256") or _sha256(raw),
        "size_bytes": len(raw),
        "truncated": bool(row.get("log_truncated")),
    }
    payload = {
        "format": LOG_FORMAT,
        "node_id": node,
        "controller_id": controller,
        "attempt_id": row["attempt_id"],
        "request_sha256": row["request_sha256"],
        "log": descriptor,
        "offset_bytes": offset,
        "length_bytes": len(chunk),
        "data_base64": base64.b64encode(chunk).decode("ascii"),
        "chunk_sha256": _sha256(chunk),
        "eof": offset + len(chunk) == len(raw),
    }
    return 200, json.dumps(payload, separators=(",", ":")).encode()


# ---------------------------------------------------------------------------
# Capabilities and submission. The lifecycle (runner, acceptance, settlement) is the service's.


def capabilities() -> tuple[int, bytes]:
    """The packaged runner's exact capabilities; 503 when no runner is packaged.

    A runner without a full-runner stop proof answers 200 with ``supported=false`` and a reason,
    so a caller can read the packaged policy/model/fixture identity without being told the node
    is unreachable.
    """
    from . import developer_test_service
    return developer_test_service.capabilities()


def submit(raw: bytes, key: str, *, actor: dict | None = None,
           destination: str | None = None) -> tuple[int, bytes]:
    """Reconcile an accepted key first, then validate pins and durably accept through the service.

    The replay binding uses the same authenticated actor scope and destination as
    :func:`accept_attempt`; a different identity on the same key conflicts rather than launching
    a second workload.
    """
    existing = attempt(key)
    if existing is not None:
        if existing.get("request_sha256") != _sha256(raw):
            raise Refused(409, "conflict",
                          "idempotency key was already used for different exact bytes")
        if actor is not None and destination is not None:
            scope = f"{actor.get('kind') or ''}:{actor.get('node_id') or ''}"
            if (existing.get("actor_scope") != scope
                    or existing.get("destination") != destination):
                raise Refused(409, "conflict",
                              "idempotency key was already used for a different attempt identity")
        # Exact replay reconciles the original attempt even after OFF or a revision change.
        return 200, json.dumps(_status_of(existing), separators=(",", ":")).encode()
    request = validate_request(parse_json(raw))
    record, error = _load_setting()
    if error is not None:
        raise error
    if not record["verified"]:
        raise Refused(503, "integrity_error", "Developer Mode is not verified on this Mac")
    if not record["enabled"]:
        raise Refused(409, "disabled", "Developer Mode is off on this Mac")
    if request["node_setting_revision"] != record["revision"]:
        raise Refused(409, "stale_setting",
                      "the request pins a setting revision this Mac no longer holds")
    node, _controller = _require_identity()
    from . import developer_test_service
    return developer_test_service.submit_validated(
        raw, key, request, record,
        actor=actor or {"kind": "hub", "node_id": None},
        destination=destination or node)


# ---------------------------------------------------------------------------
# HTTP routes. The controller relays these exact suffixes to the node's `local` path.


def _query_pairs(raw: str) -> list[tuple[str, str]]:
    from urllib.parse import parse_qsl
    return parse_qsl(raw, keep_blank_values=True, strict_parsing=False)


def _log_query(raw: str) -> tuple[int, int]:
    pairs = _query_pairs(raw)
    names = [name for name, _ in pairs]
    if sorted(names) != ["length_bytes", "offset_bytes"]:
        raise Refused(400, "invalid_request",
                      "log query must contain exactly offset_bytes and length_bytes")
    values = dict(pairs)
    try:
        offset = int(values["offset_bytes"])
        length = int(values["length_bytes"])
    except (TypeError, ValueError):
        raise Refused(400, "invalid_request", "log bounds must be integers") from None
    if offset < 0 or offset > MAX_INT or length < 1 or length > MAX_INT:
        raise Refused(400, "invalid_request", "log bounds are outside their ranges")
    return offset, length


def _no_query(request: Request) -> None:
    if request.url.query:
        raise Refused(400, "invalid_request", "this route takes no query")


async def _body(request: Request) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > REQUEST_BYTES_CEILING:
            raise Refused(413, "invalid_request", "request body is over the wire ceiling")
        chunks.append(chunk)
    return b"".join(chunks)


def _actor(request: Request) -> dict:
    actor = getattr(request.state, "fleet_actor", None)
    if actor:
        return actor
    # The auth middleware deliberately lets loopback through without binding an actor. That
    # shortcut must not turn a *supplied* node credential into the owner: classify the token
    # exactly as the middleware would, and refuse anything that is not a Hub or Fleet token.
    try:
        from .backend import auth, peers
        offered = auth.presented_token(request)
        if offered:
            node_actor = auth._node_actor(offered)
            if node_actor is not None:
                return node_actor
            if auth.valid_browser_session(offered):
                return {"kind": "hub", "node_id": None}
            hub_token = auth.load_token()
            if hub_token and secrets.compare_digest(offered, hub_token):
                return {"kind": "hub", "node_id": None}
            fleet = peers.fleet_token()
            if fleet and secrets.compare_digest(offered, fleet):
                return {"kind": "fleet", "node_id": None}
            return {"kind": None, "node_id": None}
        if auth.is_loopback(request):
            return {"kind": "hub", "node_id": None}
    except Exception:  # noqa: BLE001 - an unimportable check refuses, never permits
        pass
    return {"kind": None, "node_id": None}


def _require_actor(request: Request) -> None:
    kind = str(_actor(request).get("kind") or "")
    if kind == "node":
        raise Refused(403, "unauthorized", "a node credential cannot authorise Developer Mode")
    if kind not in ("hub", "fleet"):
        raise Refused(403, "unauthorized", "this credential cannot authorise Developer Mode")


def _key(key: str) -> str:
    if not SAFE_SEGMENT.fullmatch(str(key or "")):
        raise Refused(400, "invalid_request", "key is not a safe segment")
    return key


def _local(operation: str, request: Request, raw: bytes, key: str | None = None) -> tuple[int, bytes]:
    _require_identity()
    if operation == "capabilities":
        _no_query(request)
        return capabilities()
    if operation == "setting_get":
        _no_query(request)
        return setting_state()
    if operation == "setting_post":
        _no_query(request)
        return change_setting(raw)
    if operation == "submit":
        _no_query(request)
        node, _controller = _require_identity()
        return submit(raw, _key(key or request.headers.get("Idempotency-Key") or ""),
                      actor=_actor(request), destination=node)
    if operation == "status":
        _no_query(request)
        return attempt_status(_key(key))
    if operation == "cancel":
        _no_query(request)
        return cancel_attempt(_key(key), raw)
    if operation == "receipt":
        _no_query(request)
        return attempt_receipt(_key(key))
    if operation == "log":
        offset, length = _log_query(request.url.query)
        return attempt_log(_key(key), offset, length)
    raise Refused(404, "unknown_node", "unknown Developer Mode operation")


def _local_ids() -> tuple[str, ...]:
    from .backend import fleet_nodes
    return ("local", str(fleet_nodes.this_machine().get("id") or "").lower())


async def _relay(node_id: str, method: str, suffix: str, raw: bytes,
                 request: Request) -> tuple[int, bytes]:
    from .backend import fleet_nodes
    wanted = str(node_id or "").strip().lower()
    node = fleet_nodes._read().get(wanted)
    if node is None:
        raise Refused(404, "unknown_node",
                      f"this controller has no joined Mac {wanted!r}")
    headers = {}
    for name in ("Idempotency-Key", "Content-Type"):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    client = fleet_nodes._probe_client()
    try:
        query = f"?{request.url.query}" if request.url.query else ""
        response = await client.request(
            method,
            f"{node.get('address') or ''}/api/hub/fleet/nodes/local/developer-tests"
            f"{suffix}{query}",
            content=raw or None,
            headers=headers,
            timeout=fleet_nodes._dial_timeout(node, fleet_nodes.STARTUP_RELAY_TIMEOUT),
        )
    except httpx.HTTPError as exc:
        raise Refused(502, "transport_error",
                      "that Mac did not answer, so acceptance is unknown",
                      retryable=True, acceptance="unknown") from exc
    finally:
        await client.aclose()
    if response.status_code in (401, 403):
        raise Refused(403, "unauthorized",
                      f"that Mac refused the Developer Mode request (HTTP {response.status_code})")
    if len(response.content) > RESPONSE_BYTES_CEILING:
        raise Refused(502, "transport_error",
                      "that Mac's answer was over the bounded response ceiling",
                      retryable=True, acceptance="unknown")
    return response.status_code, response.content


async def _dispatch(node_id: str, request: Request, operation: str,
                    key: str | None = None, suffix: str = "") -> Response:
    raw = b""
    if request.method == "POST":
        try:
            raw = await _body(request)
        except Refused as exc:
            status, body = _refusal(exc, key)
            return Response(content=body, status_code=status, media_type="application/json")
    try:
        # Before either branch: a node credential must never authorise or relay a test.
        _require_actor(request)
        if str(node_id).strip().lower() in _local_ids():
            status, body = _local(operation, request, raw, key)
        else:
            status, body = await _relay(node_id, request.method, suffix, raw, request)
    except Refused as exc:
        status, body = _refusal(exc, key)
    return Response(content=body, status_code=status, media_type="application/json")


def register_routes(app) -> None:
    """The node's own fixed Developer Mode surface. Nothing else is reachable."""

    @app.get("/api/hub/fleet/nodes/{node_id}/developer-tests/capabilities")
    async def developer_tests_capabilities(node_id: str, request: Request) -> Response:
        return await _dispatch(node_id, request, "capabilities", suffix="/capabilities")

    @app.get("/api/hub/fleet/nodes/{node_id}/developer-tests/setting")
    async def developer_tests_setting_get(node_id: str, request: Request) -> Response:
        return await _dispatch(node_id, request, "setting_get", suffix="/setting")

    @app.post("/api/hub/fleet/nodes/{node_id}/developer-tests/setting")
    async def developer_tests_setting_post(node_id: str, request: Request) -> Response:
        return await _dispatch(node_id, request, "setting_post", suffix="/setting")

    @app.post("/api/hub/fleet/nodes/{node_id}/developer-tests")
    async def developer_tests_submit(node_id: str, request: Request) -> Response:
        return await _dispatch(node_id, request, "submit",
                               key=request.headers.get("Idempotency-Key"))

    @app.get("/api/hub/fleet/nodes/{node_id}/developer-tests/by-key/{key}")
    async def developer_tests_status(node_id: str, key: str, request: Request) -> Response:
        return await _dispatch(node_id, request, "status", key=key, suffix=f"/by-key/{key}")

    @app.post("/api/hub/fleet/nodes/{node_id}/developer-tests/by-key/{key}/cancel")
    async def developer_tests_cancel(node_id: str, key: str, request: Request) -> Response:
        return await _dispatch(node_id, request, "cancel", key=key,
                               suffix=f"/by-key/{key}/cancel")

    @app.get("/api/hub/fleet/nodes/{node_id}/developer-tests/by-key/{key}/receipt")
    async def developer_tests_receipt(node_id: str, key: str, request: Request) -> Response:
        return await _dispatch(node_id, request, "receipt", key=key,
                               suffix=f"/by-key/{key}/receipt")

    @app.get("/api/hub/fleet/nodes/{node_id}/developer-tests/by-key/{key}/log")
    async def developer_tests_log(node_id: str, key: str, request: Request) -> Response:
        return await _dispatch(node_id, request, "log", key=key,
                               suffix=f"/by-key/{key}/log")
