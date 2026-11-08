"""Frozen app-gate pilot closed contract validation and safe tar foundations.

Normative pair: ``contracts/developer-gate-v1.schema.json`` and ``contracts/developer-gate-v1-wire.txt``.
Revision: studio.developer-gate.v1 revision 2.

This module provides:
1. Strict UTF-8 JSON parsing with duplicate-key and non-finite rejection.
2. Canonical JSON serialization and request SHA-256 computation.
3. Lightweight stdlib JSON Schema validator for the closed schema subset.
4. Semantic request validation against advertised capabilities and owner policy pins.
5. Bounded source tar validation and safe extraction into empty owned directories.
6. Bounded result archive validation (exact manifest.json and declared logs only).

No HTTP wiring, no network, and no test execution is performed here.
"""

from __future__ import annotations

import functools
import hashlib
import io
import json
import math
import os
from pathlib import Path
import posixpath
import re
import tarfile
from typing import Any, BinaryIO, Callable, Mapping, Sequence
import unicodedata

CONTRACT_ID = "studio.developer-gate.v1"
CONTRACT_REVISION = "revision2"

# Closed format constants
REQUEST_FORMAT = "studio.developer-gate.request.v1"
CAPABILITIES_FORMAT = "studio.developer-gate.capabilities.v1"
POLICY_FORMAT = "studio.developer-gate.policy.v1"
STATUS_FORMAT = "studio.developer-gate.status.v1"
CANCEL_FORMAT = "studio.developer-gate.cancel.v1"
ACK_FORMAT = "studio.developer-gate.ack.v1"
RESULT_FORMAT = "studio.developer-gate.result.v1"
ERROR_FORMAT = "studio.developer-gate.error.v1"
INVENTORY_FORMAT = "studio.developer-gate.inventory.v1"
TOOL_MANIFEST_FORMAT = "studio.tool-manifest.v1"

MEDIA_TYPE_TAR = "application/x-tar"

SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
COMMIT_HEX = re.compile(r"^[0-9a-f]{40}$")
MAX_SAFE_INT = 9007199254740991
STREAM_CHUNK_BYTES = 64 * 1024

ABSOLUTE_MAX_MEMBERS = 4096
ABSOLUTE_MAX_PATH_LENGTH = 1024
ABSOLUTE_MAX_JSON_BYTES = 4 * 1024 * 1024
ABSOLUTE_MAX_LOG_BYTES = 32 * 1024 * 1024
ABSOLUTE_MAX_AGGREGATE_LOG_BYTES = 128 * 1024 * 1024
MAX_METADATA_HEADER_BYTES = 64 * 1024


class BoundedTarInfo(tarfile.TarInfo):
    """TarInfo subclass enforcing strict small finite metadata header byte bounds."""

    def _proc_member(self, tarfile_obj: tarfile.TarFile) -> tarfile.TarInfo:
        if self.type in (tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK):
            if self.size > ABSOLUTE_MAX_PATH_LENGTH + 512:
                raise GateContractError("GNU long metadata exceeds size ceiling", status=413, code="bounds")
        elif self.type in (tarfile.XHDTYPE, tarfile.XGLTYPE, getattr(tarfile, "SOLARIS_XHDTYPE", b"X")):
            if self.size > MAX_METADATA_HEADER_BYTES:
                raise GateContractError("PAX metadata header exceeds size ceiling", status=413, code="bounds")
        elif self.type == getattr(tarfile, "GNUTYPE_SPARSE", b"S"):
            raise GateContractError("Sparse files are forbidden", status=400, code="invalid_request")
        return super()._proc_member(tarfile_obj)


class BoundedTarFile(tarfile.TarFile):
    """TarFile using BoundedTarInfo to bound metadata before reading."""
    tarinfo = BoundedTarInfo


def open_bounded_tar(name: str | Path | None = None, mode: str = "r:", fileobj: BinaryIO | None = None) -> BoundedTarFile:
    """Open tar archive using BoundedTarFile to protect against metadata allocation attacks."""
    if name is not None:
        return BoundedTarFile.open(name=str(name), mode=mode)
    return BoundedTarFile.open(mode=mode, fileobj=fileobj)


def _write_all(fd: int, data: bytes) -> None:
    """Write all bytes to file descriptor, handling potential short writes."""
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written == 0:
            raise GateContractError("Short write encountered during extraction")
        offset += written


class GateContractError(Exception):
    """Explicit refusal or contract violation error."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 400,
        code: str = "malformed",
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.detail = detail or message

    def to_error_dict(self, *, acceptance: dict[str, Any] | None = None) -> dict[str, Any]:
        """Format as closed error schema (studio.developer-gate.error.v1)."""
        return {
            "format": ERROR_FORMAT,
            "reason": {
                "code": self.code,
                "detail": self.detail,
            },
            "acceptance": acceptance,
        }


# Backwards compatibility alias
DeveloperGateError = GateContractError


# ---------------------------------------------------------------------------
# Strict JSON and Canonical Hashing
# ---------------------------------------------------------------------------


def parse_strict_json(raw: bytes | str, *, max_bytes: int | None = None) -> Any:
    """Parse JSON with strict UTF-8 decoding, duplicate key rejection, and nonfinite rejection.

    Rejects duplicate object keys, NaN, Infinity, -Infinity, and invalid UTF-8.
    """
    if isinstance(raw, str):
        raw_bytes = raw.encode("utf-8")
    elif isinstance(raw, (bytes, bytearray, memoryview)):
        raw_bytes = bytes(raw)
        try:
            raw = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GateContractError(f"Invalid UTF-8 encoding in JSON: {exc}") from exc
    else:
        raise GateContractError(f"Expected bytes or str for JSON input, got {type(raw).__name__}")

    if max_bytes is not None and len(raw_bytes) > max_bytes:
        raise GateContractError(
            f"JSON payload {len(raw_bytes)} bytes exceeds ceiling {max_bytes} bytes",
            status=413,
            code="bounds",
        )

    def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        res: dict[str, Any] = {}
        for k, v in pairs:
            if k in res:
                raise GateContractError(f"Duplicate key in JSON object: {k!r}")
            res[k] = v
        return res

    def _reject_constant(const: str) -> None:
        raise GateContractError(f"Non-finite JSON constant not permitted: {const!r}")

    try:
        return json.loads(
            raw,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise GateContractError(f"Malformed JSON: {exc}") from exc


def canonical_json(data: Any) -> str:
    """Produce canonical JSON: sorted keys, separators (',', ':'), ensure_ascii=True, allow_nan=False."""
    try:
        return json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise GateContractError(f"Cannot serialize to canonical JSON: {exc}") from exc


def compute_canonical_sha256(data: Any) -> str:
    """Compute SHA-256 of canonical JSON."""
    canon = canonical_json(data)
    return hashlib.sha256(canon.encode("ascii")).hexdigest()


def sha256_stream(stream: BinaryIO, *, max_bytes: int | None = None) -> tuple[str, int]:
    """Compute SHA-256 and byte length of a stream with an optional ceiling."""
    hasher = hashlib.sha256()
    total_bytes = 0
    while True:
        chunk = stream.read(STREAM_CHUNK_BYTES)
        if not chunk:
            break
        total_bytes += len(chunk)
        if max_bytes is not None and total_bytes > max_bytes:
            raise GateContractError(
                f"Stream exceeded maximum size of {max_bytes} bytes",
                status=413,
                code="bounds",
            )
        hasher.update(chunk)
    return hasher.hexdigest(), total_bytes


def sha256_file(path: Path | str, *, max_bytes: int | None = None) -> tuple[str, int]:
    """Compute SHA-256 and byte length of a file on disk."""
    p = Path(path)
    with open(p, "rb") as f:
        return sha256_stream(f, max_bytes=max_bytes)


# ---------------------------------------------------------------------------
# Schema Validation (Minimal Pure-Stdlib Validator for Closed Contract Subset)
# ---------------------------------------------------------------------------

_SCHEMA_CACHE: dict[str, Any] | None = None


def load_gate_schema(schema_path: Path | str | None = None) -> dict[str, Any]:
    """Load canonical schema doc from repo contracts/ directory or specified path."""
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is not None and schema_path is None:
        return _SCHEMA_CACHE

    if schema_path is None:
        # Resolves relative to this module in components/hub/ -> root / contracts / developer-gate-v1.schema.json
        base_dir = Path(__file__).resolve().parents[2]
        target = base_dir / "contracts" / "developer-gate-v1.schema.json"
        if not target.exists():
            # Try alternate worktree/repo paths
            fallback = Path.cwd() / "contracts" / "developer-gate-v1.schema.json"
            if fallback.exists():
                target = fallback
            else:
                raise GateContractError(f"Schema file not found at {target}")
    else:
        target = Path(schema_path)

    with open(target, "r", encoding="utf-8") as f:
        loaded = json.load(f)

    if schema_path is None:
        _SCHEMA_CACHE = loaded
    return loaded


_FORMAT_TO_DEF: dict[str, str] = {
    REQUEST_FORMAT: "gateRequest",
    CAPABILITIES_FORMAT: "capabilities",
    POLICY_FORMAT: "policy",
    STATUS_FORMAT: "status",
    CANCEL_FORMAT: "cancelRequest",
    ACK_FORMAT: "ackRequest",
    RESULT_FORMAT: "resultManifest",
    ERROR_FORMAT: "error",
    INVENTORY_FORMAT: "inventory",
    TOOL_MANIFEST_FORMAT: "toolManifest",
}


def _validate_node(
    instance: Any,
    sub_schema: dict[str, Any],
    defs: dict[str, Any],
    path: str = "$",
) -> None:
    if "$ref" in sub_schema:
        ref = sub_schema["$ref"]
        if not ref.startswith("#/$defs/"):
            raise GateContractError(f"Unsupported schema ref {ref!r} at {path}")
        def_name = ref[len("#/$defs/") :]
        if def_name not in defs:
            raise GateContractError(f"Undefined schema definition {def_name!r} at {path}")
        _validate_node(instance, defs[def_name], defs, path)
        return

    if "anyOf" in sub_schema:
        matched = False
        reasons: list[str] = []
        for alt in sub_schema["anyOf"]:
            try:
                _validate_node(instance, alt, defs, path)
                matched = True
                break
            except GateContractError as err:
                reasons.append(str(err))
        if not matched:
            raise GateContractError(f"Value at {path} did not match any allowed alternative: {'; '.join(reasons[:2])}")
        return

    if "const" in sub_schema:
        expected = sub_schema["const"]
        if instance != expected:
            raise GateContractError(f"Expected {expected!r} at {path}, got {instance!r}")

    if "enum" in sub_schema:
        allowed = sub_schema["enum"]
        if instance not in allowed:
            raise GateContractError(f"Value {instance!r} at {path} not in allowed enum {allowed}")

    if "type" in sub_schema:
        expected_type = sub_schema["type"]
        if expected_type == "null":
            if instance is not None:
                raise GateContractError(f"Expected null at {path}, got {type(instance).__name__}")
        elif expected_type == "boolean":
            if not isinstance(instance, bool):
                raise GateContractError(f"Expected boolean at {path}, got {type(instance).__name__}")
        elif expected_type == "integer":
            if isinstance(instance, bool) or not isinstance(instance, int):
                raise GateContractError(f"Expected integer at {path}, got {type(instance).__name__}")
        elif expected_type == "number":
            if isinstance(instance, bool) or not isinstance(instance, (int, float)) or not math.isfinite(instance):
                raise GateContractError(f"Expected finite number at {path}, got {instance!r}")
        elif expected_type == "string":
            if not isinstance(instance, str):
                raise GateContractError(f"Expected string at {path}, got {type(instance).__name__}")
        elif expected_type == "object":
            if not isinstance(instance, dict):
                raise GateContractError(f"Expected object at {path}, got {type(instance).__name__}")
        elif expected_type == "array":
            if not isinstance(instance, list):
                raise GateContractError(f"Expected array at {path}, got {type(instance).__name__}")

    if isinstance(instance, str):
        if "pattern" in sub_schema:
            pat = sub_schema["pattern"]
            if not re.search(pat, instance):
                raise GateContractError(f"String at {path} does not match required pattern {pat!r}")
        if "minLength" in sub_schema and len(instance) < sub_schema["minLength"]:
            raise GateContractError(f"String at {path} length {len(instance)} < minLength {sub_schema['minLength']}")
        if "maxLength" in sub_schema and len(instance) > sub_schema["maxLength"]:
            raise GateContractError(f"String at {path} length {len(instance)} > maxLength {sub_schema['maxLength']}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in sub_schema and instance < sub_schema["minimum"]:
            raise GateContractError(f"Number at {path} value {instance} < minimum {sub_schema['minimum']}")
        if "maximum" in sub_schema and instance > sub_schema["maximum"]:
            raise GateContractError(f"Number at {path} value {instance} > maximum {sub_schema['maximum']}")

    if isinstance(instance, list):
        if "minItems" in sub_schema and len(instance) < sub_schema["minItems"]:
            raise GateContractError(f"Array at {path} item count {len(instance)} < minItems {sub_schema['minItems']}")
        if "maxItems" in sub_schema and len(instance) > sub_schema["maxItems"]:
            raise GateContractError(f"Array at {path} item count {len(instance)} > maxItems {sub_schema['maxItems']}")
        if "items" in sub_schema:
            item_schema = sub_schema["items"]
            for idx, item in enumerate(instance):
                _validate_node(item, item_schema, defs, f"{path}[{idx}]")

    if isinstance(instance, dict):
        if "required" in sub_schema:
            for req_key in sub_schema["required"]:
                if req_key not in instance:
                    raise GateContractError(f"Missing required property {req_key!r} at {path}")
        props = sub_schema.get("properties", {})
        allow_additional = sub_schema.get("additionalProperties", False)
        for key, val in instance.items():
            if key in props:
                _validate_node(val, props[key], defs, f"{path}.{key}")
            elif not allow_additional:
                raise GateContractError(f"Unrecognized property {key!r} not permitted at {path}")


def validate_schema(
    instance: Any,
    schema_or_def_name: str | dict[str, Any],
    *,
    schema_doc: dict[str, Any] | None = None,
) -> None:
    """Validate instance against loaded schema doc or specific def name."""
    doc = schema_doc or load_gate_schema()
    defs = doc.get("$defs", {})

    if isinstance(schema_or_def_name, str):
        def_name = _FORMAT_TO_DEF.get(schema_or_def_name, schema_or_def_name)
        if def_name not in defs:
            raise GateContractError(f"Unknown schema definition: {schema_or_def_name}")
        sub_schema = defs[def_name]
    else:
        sub_schema = schema_or_def_name

    _validate_node(instance, sub_schema, defs, path="$")


def validate_gate_request_schema(req: dict[str, Any]) -> None:
    validate_schema(req, "gateRequest")


def validate_capabilities_schema(caps: dict[str, Any]) -> None:
    validate_schema(caps, "capabilities")


def validate_policy_schema(pol: dict[str, Any]) -> None:
    validate_schema(pol, "policy")


def validate_result_manifest_schema(manifest: dict[str, Any]) -> None:
    validate_schema(manifest, "resultManifest")


def validate_status_schema(status: dict[str, Any]) -> None:
    validate_schema(status, "status")


def validate_cancel_request_schema(req: dict[str, Any]) -> None:
    validate_schema(req, "cancelRequest")


def validate_ack_request_schema(req: dict[str, Any]) -> None:
    validate_schema(req, "ackRequest")


def validate_inventory_schema(inv: dict[str, Any]) -> None:
    validate_schema(inv, "inventory")


def validate_error_schema(err: dict[str, Any]) -> None:
    validate_schema(err, "error")


# ---------------------------------------------------------------------------
# Semantic Request Validation against Advertised Capabilities & Policy
# ---------------------------------------------------------------------------


def _check_no_urls_or_commands(val: Any, field_name: str) -> None:
    if isinstance(val, str):
        lower = val.lower()
        if lower.startswith(("http://", "https://", "file://", "ftp://", "ssh://")):
            raise GateContractError(f"URL scheme not permitted in caller field {field_name!r}: {val}")
        if any(c in val for c in ("\r", "\n", "\0")):
            raise GateContractError(f"Control characters not permitted in field {field_name!r}")


def validate_request_semantics(
    request: dict[str, Any],
    capabilities: dict[str, Any],
    *,
    target_controller_id: str | None = None,
    target_node_id: str | None = None,
) -> None:
    """Validate gate request against packaged capabilities and policy pins.

    Enforces full equality for gate, tool manifest, source, revisions, and limits.
    Rejects caller URLs, extra arguments, unadvertised gates, and policy violations.
    """
    # 0. Rejection of caller URLs/commands in identifiers before schema validation
    for field in ("run_id", "test_id"):
        if field in request:
            _check_no_urls_or_commands(request[field], field)

    validate_gate_request_schema(request)
    validate_capabilities_schema(capabilities)

    # 1. Capabilities must advertise supported=True
    if not capabilities.get("supported"):
        reason = capabilities.get("reason")
        detail = reason.get("detail") if isinstance(reason, dict) else str(reason)
        raise GateContractError(
            f"Node does not support developer gates: {detail}",
            status=503,
            code="unsupported",
        )

    # 2. Node setting revision, enabled, and verified check
    setting = capabilities.get("setting", {})
    if not setting.get("enabled"):
        raise GateContractError("Node developer mode setting is disabled", status=409, code="disabled")
    if not setting.get("verified"):
        raise GateContractError("Node developer mode setting is unverified", status=409, code="disabled")
    if request["node_setting_revision"] != setting.get("revision"):
        raise GateContractError(
            f"Node setting revision mismatch: request={request['node_setting_revision']!r} != node={setting.get('revision')!r}",
            status=409,
            code="stale_setting",
        )

    # 3. Runner equality
    if request["runner"] != capabilities.get("runner"):
        raise GateContractError(
            f"Runner identity mismatch: request={request['runner']!r} != capabilities={capabilities.get('runner')!r}",
            status=400,
            code="invalid_request",
        )

    # 4. Admission policy revision and minimum internal physical bytes
    # Note: caller cannot lower minimum_internal_physical_bytes below policy.
    # Physical device actual inspection and fresh filesystem free checks remain runtime responsibility.
    policy = capabilities.get("admission_policy", {})
    if request["admission_policy_revision"] != policy.get("revision"):
        raise GateContractError(
            f"Admission policy revision mismatch: request={request['admission_policy_revision']!r} != node={policy.get('revision')!r}",
            status=409,
            code="conflict",
        )
    if request["minimum_internal_physical_bytes"] != policy.get("minimum_internal_physical_bytes"):
        raise GateContractError(
            f"Minimum internal physical bytes mismatch: request={request['minimum_internal_physical_bytes']} != policy={policy.get('minimum_internal_physical_bytes')}",
            status=409,
            code="conflict",
        )

    # 5. Target eligibility (exact match to capabilities and membership in allowed_targets; excluded_targets wins)
    node_id = capabilities.get("node_id")
    controller_id = capabilities.get("controller_id")
    if target_node_id is not None and target_node_id != node_id:
        raise GateContractError(
            f"Target node_id mismatch: caller={target_node_id!r} != node={node_id!r}",
            status=409,
            code="conflict",
        )
    if target_controller_id is not None and target_controller_id != controller_id:
        raise GateContractError(
            f"Target controller_id mismatch: caller={target_controller_id!r} != controller={controller_id!r}",
            status=409,
            code="conflict",
        )

    # Check against policy target lists; excluded wins, and empty allowed_targets means none permitted
    excluded = policy.get("excluded_targets", [])
    if any(t.get("controller_id") == controller_id and t.get("node_id") == node_id for t in excluded):
        raise GateContractError(
            f"Target ({controller_id}, {node_id}) is explicitly excluded by admission policy",
            status=409,
            code="conflict",
        )

    allowed = policy.get("allowed_targets", [])
    if not any(t.get("controller_id") == controller_id and t.get("node_id") == node_id for t in allowed):
        raise GateContractError(
            f"Target ({controller_id}, {node_id}) is not in admission policy allowed_targets",
            status=409,
            code="conflict",
        )

    # 6. Gate matching in advertised gates (no duplicate advertised gates)
    advertised_gates = capabilities.get("gates", [])
    adv_gate_ids = [g.get("id") for g in advertised_gates]
    if len(adv_gate_ids) != len(set(adv_gate_ids)):
        raise GateContractError("Duplicate gate identifiers advertised in capabilities", status=400, code="invalid_request")

    gate_id = request["gate"]["id"]
    matching_gate = next((g for g in advertised_gates if g.get("id") == gate_id), None)
    if matching_gate is None:
        raise GateContractError(f"Gate {gate_id!r} is not advertised by node", status=400, code="invalid_request")
    if request["gate"] != matching_gate:
        raise GateContractError(f"Gate object does not match advertised gate {gate_id!r} exactly", status=400, code="invalid_request")

    # Check for duplicate check IDs in gate
    check_ids = request["gate"].get("check_ids", [])
    if len(check_ids) != len(set(check_ids)):
        raise GateContractError("Duplicate check IDs present in gate definition", status=400, code="invalid_request")

    # 7. Source equality
    if request["source"] != request["gate"]["source"]:
        raise GateContractError("request.source does not equal request.gate.source", status=400, code="invalid_request")

    # 8. Tool manifest equality (no duplicate advertised tool manifests)
    advertised_manifests = capabilities.get("tool_manifests", [])
    adv_tm_ids = [tm.get("id") for tm in advertised_manifests]
    if len(adv_tm_ids) != len(set(adv_tm_ids)):
        raise GateContractError("Duplicate tool manifest identifiers advertised in capabilities", status=400, code="invalid_request")

    tool_manifest_id = request["tool_manifest"]["id"]
    matching_tool_manifest = next((tm for tm in advertised_manifests if tm.get("id") == tool_manifest_id), None)
    if matching_tool_manifest is None:
        raise GateContractError(f"Tool manifest {tool_manifest_id!r} is not advertised by node", status=400, code="invalid_request")
    if request["tool_manifest"] != matching_tool_manifest:
        raise GateContractError(
            f"request.tool_manifest does not match advertised tool manifest {tool_manifest_id!r} exactly",
            status=400,
            code="invalid_request",
        )
    if request["tool_manifest"] != request["gate"]["tool_manifest"]:
        raise GateContractError(
            "request.tool_manifest does not equal request.gate.tool_manifest",
            status=400,
            code="invalid_request",
        )

    # ToolManifest.tools must exactly match gate required_toolchains
    if request["tool_manifest"]["tools"] != request["gate"]["required_toolchains"]:
        raise GateContractError(
            "tool_manifest.tools does not match gate.required_toolchains exactly",
            status=400,
            code="invalid_request",
        )
    tool_ids = [t.get("id") for t in request["tool_manifest"]["tools"]]
    if len(tool_ids) != len(set(tool_ids)):
        raise GateContractError("Duplicate tool identifiers in tool manifest", status=400, code="invalid_request")

    # 9. Requested limits within gate limits and node bounds
    # For resource ceilings, requested cannot exceed gate limits or node bounds.
    # For safety floors (minimum_free_bytes, start_min_free_memory_bytes), caller cannot lower below gate values.
    requested = request["requested_limits"]
    gate_limits = request["gate"]["limits"]
    bounds = capabilities["bounds"]

    comparisons = (
        ("runtime_seconds", bounds["runtime_seconds"], gate_limits["runtime_seconds"]),
        ("workspace_bytes", bounds["workspace_bytes"], gate_limits["workspace_bytes"]),
        ("result_spool_bytes", bounds["result_spool_bytes"], gate_limits["result_spool_bytes"]),
        ("shared_cache_bytes", bounds["shared_cache_bytes"], gate_limits["shared_cache_bytes"]),
        ("toolchain_install_bytes", bounds["toolchain_install_bytes"], gate_limits["toolchain_install_bytes"]),
        ("max_process_tree_rss_bytes", bounds["max_process_tree_rss_bytes"], gate_limits["max_process_tree_rss_bytes"]),
        ("max_system_swap_growth_bytes", bounds["max_system_swap_growth_bytes"], gate_limits["max_system_swap_growth_bytes"]),
    )

    for field, bound_val, gate_val in comparisons:
        req_val = requested[field]
        if req_val > bound_val:
            raise GateContractError(
                f"Requested limit {field}={req_val} exceeds node bound {bound_val}",
                status=413,
                code="bounds",
            )
        if req_val > gate_val:
            raise GateContractError(
                f"Requested limit {field}={req_val} exceeds gate manifest limit {gate_val}",
                status=413,
                code="bounds",
            )

    # Safety floors: caller cannot lower below gate minimums
    if requested["minimum_free_bytes"] < gate_limits["minimum_free_bytes"]:
        raise GateContractError(
            f"Requested limit minimum_free_bytes={requested['minimum_free_bytes']} cannot be lower than gate limit {gate_limits['minimum_free_bytes']}",
            status=400,
            code="invalid_request",
        )
    if requested["start_min_free_memory_bytes"] < gate_limits["start_min_free_memory_bytes"]:
        raise GateContractError(
            f"Requested limit start_min_free_memory_bytes={requested['start_min_free_memory_bytes']} cannot be lower than gate limit {gate_limits['start_min_free_memory_bytes']}",
            status=400,
            code="invalid_request",
        )

    # 10. Rejection of caller URLs/commands in identifiers
    for field in ("run_id", "test_id"):
        _check_no_urls_or_commands(request[field], field)


# ---------------------------------------------------------------------------
# Bounded Source Tar Validation & Safe Extraction
# ---------------------------------------------------------------------------


def _validate_safe_member_path(
    raw_name: str,
    seen_normalized: set[str],
    seen_casefold: set[str],
) -> str:
    """Validate relative path safety against traversal, absolute paths, and aliasing."""
    if len(raw_name) > ABSOLUTE_MAX_PATH_LENGTH:
        raise GateContractError("Member path length exceeds limit")
    if raw_name.startswith(("/", "\\")):
        raise GateContractError("Absolute member path forbidden")
    if ":" in raw_name:
        raise GateContractError("Drive or stream specifier forbidden in member path")

    norm = posixpath.normpath(raw_name)
    if norm == ".":
        return "."
    if norm.startswith("../") or norm == "..":
        raise GateContractError("Path traversal forbidden")
    if norm.startswith("/"):
        raise GateContractError("Path resolves to root")

    parts = norm.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise GateContractError(f"Invalid path segments in member: {raw_name!r}")

    # Check NFC unicode normalization
    nfc = unicodedata.normalize("NFC", norm)
    if nfc != norm:
        raise GateContractError(f"Member path is not NFC normalized: {raw_name!r}")

    # Duplicate and aliasing check
    if norm in seen_normalized:
        raise GateContractError(f"Duplicate or aliased member path: {norm!r}")
    seen_normalized.add(norm)

    cf = norm.casefold()
    if cf in seen_casefold:
        raise GateContractError(f"Case-collision or aliased member path: {norm!r}")
    seen_casefold.add(cf)

    return norm


def validate_source_archive(
    archive_path_or_stream: Path | str | BinaryIO,
    declared_source: dict[str, Any],
    *,
    bounds: dict[str, Any] | None = None,
) -> None:
    """Validate raw hash, size, and uncompressed tar members of source archive without extracting."""
    validate_schema(declared_source, "source")
    if declared_source.get("media_type") != MEDIA_TYPE_TAR:
        raise GateContractError(
            f"Unsupported source media type: {declared_source.get('media_type')!r}",
            status=400,
            code="invalid_request",
        )

    # 1. Verify raw stream/file hash and size
    declared_sha = declared_source["sha256"]
    declared_size = declared_source["size_bytes"]
    max_raw_bytes = bounds.get("source_bytes") if bounds else declared_size

    if isinstance(archive_path_or_stream, (str, Path)):
        p = Path(archive_path_or_stream)
        actual_sha, actual_size = sha256_file(p, max_bytes=max_raw_bytes)
    else:
        stream = archive_path_or_stream
        if not stream.seekable():
            raise GateContractError("Non-seekable stream not permitted for archive validation")
        stream.seek(0)
        actual_sha, actual_size = sha256_stream(stream, max_bytes=max_raw_bytes)
        stream.seek(0)

    if actual_size != declared_size:
        raise GateContractError(
            f"Source archive size mismatch: actual {actual_size} != declared {declared_size}",
            status=400,
            code="invalid_request",
        )
    if actual_sha != declared_sha:
        raise GateContractError(
            f"Source archive SHA-256 mismatch: actual {actual_sha} != declared {declared_sha}",
            status=400,
            code="invalid_request",
        )

    # 2. Inspect members incrementally BEFORE any write
    max_files = bounds.get("source_files") if bounds else declared_source["file_count"]
    max_expanded = bounds.get("source_expanded_bytes") if bounds else declared_source["expanded_bytes"]
    max_total_members = min(max_files * 2 + 64, ABSOLUTE_MAX_MEMBERS)

    if isinstance(archive_path_or_stream, (str, Path)):
        tar_ctx = open_bounded_tar(name=archive_path_or_stream, mode="r:")
    else:
        tar_ctx = open_bounded_tar(fileobj=archive_path_or_stream, mode="r:")

    file_count = 0
    expanded_bytes = 0
    total_members = 0
    seen_normalized: set[str] = set()
    seen_casefold: set[str] = set()

    with tar_ctx as tar:
        while True:
            member = tar.next()
            if member is None:
                break

            total_members += 1
            if total_members > max_total_members:
                raise GateContractError(
                    f"Total archive members count exceeds limit ({total_members} > {max_total_members})",
                    status=413,
                    code="bounds",
                )

            # Reject symlinks, hardlinks, specials, and sparse
            if member.issym():
                raise GateContractError(f"Symlinks are forbidden in source archive: {member.name}")
            if member.islnk():
                raise GateContractError(f"Hardlinks are forbidden in source archive: {member.name}")
            if member.ischr() or member.isblk() or member.isfifo():
                raise GateContractError(f"Special device/FIFO files forbidden: {member.name}")
            if member.issparse() or getattr(member, "sparse", False) or member.type in (b"S", "S"):
                raise GateContractError(f"Sparse files are forbidden in source archive: {member.name}")

            if not (member.isreg() or member.isdir()):
                raise GateContractError(f"Unsupported member type {member.type!r} for: {member.name}")

            norm = _validate_safe_member_path(member.name, seen_normalized, seen_casefold)
            if norm == "." and member.isdir():
                continue

            if member.isreg():
                if member.size < 0:
                    raise GateContractError(f"Negative member size: {member.name}")
                file_count += 1
                expanded_bytes += member.size

                if max_files is not None and file_count > max_files:
                    raise GateContractError(
                        f"Source archive file count exceeds limit ({file_count} > {max_files})",
                        status=413,
                        code="bounds",
                    )
                if max_expanded is not None and expanded_bytes > max_expanded:
                    raise GateContractError(
                        f"Source expanded bytes exceeds limit ({expanded_bytes} > {max_expanded})",
                        status=413,
                        code="bounds",
                    )

    if file_count != declared_source["file_count"]:
        raise GateContractError(
            f"Source archive file count mismatch: actual {file_count} != declared {declared_source['file_count']}",
            status=400,
            code="invalid_request",
        )
    if expanded_bytes != declared_source["expanded_bytes"]:
        raise GateContractError(
            f"Source expanded bytes mismatch: actual {expanded_bytes} != declared {declared_source['expanded_bytes']}",
            status=400,
            code="invalid_request",
        )


def extract_source_archive(
    archive_path_or_stream: Path | str | BinaryIO,
    destination_dir: Path | str,
    declared_source: dict[str, Any],
    *,
    bounds: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Validate and safely extract source archive into a NEW empty owned directory.

    Holds the same file descriptor/stream and re-validates all headers during extraction.
    Never follows existing links or allows link extraction.
    Sanitizes file permissions, removing setuid/setgid/sticky bits.
    """
    dest_path = Path(destination_dir)

    # Destination directory must not be a symlink
    if dest_path.is_symlink():
        raise GateContractError("Destination directory must not be a symlink")

    dest = dest_path.resolve()

    # Destination directory checks
    if dest.exists():
        if dest.is_symlink():
            raise GateContractError("Destination directory must not be a symlink")
        if not dest.is_dir():
            raise GateContractError("Destination exists and is not a directory")
        if any(dest.iterdir()):
            raise GateContractError("Destination directory must be empty")
    else:
        dest.mkdir(parents=True, mode=0o700)

    # Phase 1: validate in full before writing any file
    validate_source_archive(archive_path_or_stream, declared_source, bounds=bounds)

    # Phase 2: safely write regular files and directories using the same stream/fd
    if isinstance(archive_path_or_stream, (str, Path)):
        tar_ctx = open_bounded_tar(name=archive_path_or_stream, mode="r:")
    else:
        archive_path_or_stream.seek(0)
        tar_ctx = open_bounded_tar(fileobj=archive_path_or_stream, mode="r:")

    file_count = 0
    expanded_bytes = 0
    seen_normalized: set[str] = set()
    seen_casefold: set[str] = set()

    open_flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW

    with tar_ctx as tar:
        while True:
            member = tar.next()
            if member is None:
                break

            # Re-verify member safety on pass 2
            if not (member.isreg() or member.isdir()):
                raise GateContractError(f"Unsupported member type on extract: {member.name}")
            if member.issym() or member.islnk() or member.issparse() or getattr(member, "sparse", False):
                raise GateContractError(f"Forbidden member on extract: {member.name}")

            norm = _validate_safe_member_path(member.name, seen_normalized, seen_casefold)
            if norm == "." and member.isdir():
                continue

            parts = norm.split("/")
            curr = dest
            for part in parts[:-1]:
                curr = curr / part
                if curr.is_symlink():
                    raise GateContractError("Parent directory in destination is a symlink")
                if not curr.exists():
                    curr.mkdir(mode=0o755)

            if member.isdir():
                target_dir = curr / parts[-1]
                if target_dir.is_symlink():
                    raise GateContractError("Target directory is a symlink")
                if not target_dir.exists():
                    target_dir.mkdir(mode=0o755)
            elif member.isreg():
                target_file = curr / parts[-1]
                if target_file.is_symlink():
                    raise GateContractError("Target file path is a symlink")

                # Sanitize permissions: remove setuid, setgid, sticky bits (0o7000)
                # Ensure readable/writable by owner; preserve execute bit if present in source
                mode = (member.mode & 0o755) | 0o644 if (member.mode & 0o111) else (member.mode & 0o644) | 0o644
                mode &= ~0o7000

                fd = os.open(str(target_file), open_flags, mode)
                written = 0
                try:
                    fsrc = tar.extractfile(member)
                    if fsrc is None:
                        raise GateContractError(f"Unable to read tar member: {member.name}")
                    while True:
                        chunk = fsrc.read(STREAM_CHUNK_BYTES)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > member.size:
                            raise GateContractError(f"Member stream exceeded declared size: {member.name}")
                        _write_all(fd, chunk)
                finally:
                    os.close(fd)

                if written != member.size:
                    raise GateContractError(f"Incomplete write for {member.name} ({written} != {member.size})")

                file_count += 1
                expanded_bytes += written

    if file_count != declared_source["file_count"] or expanded_bytes != declared_source["expanded_bytes"]:
        raise GateContractError("Source extraction budget mismatch on pass 2", status=400, code="invalid_request")

    return {"file_count": file_count, "expanded_bytes": expanded_bytes}


# ---------------------------------------------------------------------------
# Bounded Result Tar Validation
# ---------------------------------------------------------------------------


def validate_result_archive(
    archive_path_or_stream: Path | str | BinaryIO,
    *,
    expected_acceptance: dict[str, Any],
    expected_descriptor: dict[str, Any] | None = None,
    expected_request: dict[str, Any] | None = None,
    bounds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate immutable result archive.

    Verifies:
    1. Raw archive size and SHA-256 match expected_descriptor (if provided) and stay within bounds.
    2. Plain uncompressed tar containing EXACTLY manifest.json and logs/<check-id>.log for each check.
    3. Incremental iteration checking sparse/links/types before reading; bounds reader ceilings.
    4. Manifest binds immutable acceptance to expected_acceptance and canonical request SHA.
    5. Each log is strict UTF-8 and exactly matches declared size and SHA; total logs bounded.
    6. Outcome semantics: process release confirmed on all terminal results; passed requires
       exact matching actual tools with available=true and complete checks with non-null duration.
    """
    validate_schema(expected_acceptance, "acceptance")

    max_raw_bytes = bounds.get("result_bytes") if bounds else (expected_descriptor.get("size_bytes") if expected_descriptor else None)

    # 1. Compute raw hash and size
    if isinstance(archive_path_or_stream, (str, Path)):
        p = Path(archive_path_or_stream)
        actual_sha, actual_size = sha256_file(p, max_bytes=max_raw_bytes)
    else:
        stream = archive_path_or_stream
        if not stream.seekable():
            raise GateContractError("Non-seekable stream not permitted for archive validation")
        stream.seek(0)
        actual_sha, actual_size = sha256_stream(stream, max_bytes=max_raw_bytes)
        stream.seek(0)

    if expected_descriptor is not None:
        validate_schema(expected_descriptor, "resultDescriptor")
        if actual_size != expected_descriptor["size_bytes"]:
            raise GateContractError(
                f"Result archive size {actual_size} != descriptor {expected_descriptor['size_bytes']}",
                status=400,
                code="integrity_error",
            )
        if actual_sha != expected_descriptor["sha256"]:
            raise GateContractError(
                f"Result archive SHA-256 {actual_sha} != descriptor {expected_descriptor['sha256']}",
                status=400,
                code="integrity_error",
            )

    # 2. Incremental iteration over uncompressed tar members
    max_members = min(bounds.get("result_files", ABSOLUTE_MAX_MEMBERS) if bounds else ABSOLUTE_MAX_MEMBERS, ABSOLUTE_MAX_MEMBERS)
    max_json = min(bounds.get("json_bytes", ABSOLUTE_MAX_JSON_BYTES) if bounds else ABSOLUTE_MAX_JSON_BYTES, ABSOLUTE_MAX_JSON_BYTES)
    max_single_log = min(bounds.get("log_bytes", ABSOLUTE_MAX_LOG_BYTES) if bounds else ABSOLUTE_MAX_LOG_BYTES, ABSOLUTE_MAX_LOG_BYTES)
    max_aggregate_log = min(bounds.get("result_bytes", ABSOLUTE_MAX_AGGREGATE_LOG_BYTES) if bounds else ABSOLUTE_MAX_AGGREGATE_LOG_BYTES, ABSOLUTE_MAX_AGGREGATE_LOG_BYTES)

    if isinstance(archive_path_or_stream, (str, Path)):
        tar_ctx = open_bounded_tar(name=archive_path_or_stream, mode="r:")
    else:
        tar_ctx = open_bounded_tar(fileobj=archive_path_or_stream, mode="r:")

    member_map: dict[str, tarfile.TarInfo] = {}
    member_count = 0

    with tar_ctx as tar:
        while True:
            m = tar.next()
            if m is None:
                break

            member_count += 1
            if member_count > max_members:
                raise GateContractError(
                    f"Result archive member count exceeds limit ({member_count} > {max_members})",
                    status=413,
                    code="bounds",
                )

            if m.isdir():
                raise GateContractError(f"Directory entries forbidden in result archive: {m.name}")
            if not m.isreg():
                raise GateContractError(f"Non-regular file forbidden in result archive: {m.name}")
            if m.issparse() or getattr(m, "sparse", False) or m.type in (b"S", "S"):
                raise GateContractError(f"Sparse files are forbidden in result archive: {m.name}")
            if m.name.startswith(("/", "\\")) or ".." in m.name:
                raise GateContractError(f"Invalid member path: {m.name}")

            norm = posixpath.normpath(m.name)
            if norm != m.name:
                raise GateContractError(f"Non-canonical member path: {m.name}")

            if norm in member_map:
                raise GateContractError(f"Duplicate member in result archive: {norm}")
            member_map[norm] = m

        if "manifest.json" not in member_map:
            raise GateContractError("Missing manifest.json in result archive")

        manifest_info = member_map["manifest.json"]
        if manifest_info.size > max_json:
            raise GateContractError(
                f"manifest.json size {manifest_info.size} exceeds bound {max_json}",
                status=413,
                code="bounds",
            )

        f_manifest = tar.extractfile(manifest_info)
        if f_manifest is None:
            raise GateContractError("Could not read manifest.json from result archive")
        manifest_raw = f_manifest.read()
        manifest = parse_strict_json(manifest_raw, max_bytes=max_json)
        validate_result_manifest_schema(manifest)

        # 3. Check exact acceptance binding and canonical hash
        if manifest.get("acceptance") != expected_acceptance:
            raise GateContractError(
                f"Result manifest acceptance does not match expected acceptance: {manifest.get('acceptance')!r} != {expected_acceptance!r}",
                status=400,
                code="invalid_request",
            )

        if expected_request is not None:
            canonical_req_sha = compute_canonical_sha256(expected_request)
            if manifest["acceptance"]["request_sha256"] != canonical_req_sha:
                raise GateContractError(
                    f"Result acceptance request_sha256 mismatch with canonical request: {manifest['acceptance']['request_sha256']} != {canonical_req_sha}",
                    status=400,
                    code="invalid_request",
                )
            if manifest["runner"] != expected_request.get("runner"):
                raise GateContractError("Result runner does not match accepted request")
            if manifest["gate"] != expected_request.get("gate"):
                raise GateContractError("Result gate does not match accepted request")
            if manifest["source"] != expected_request.get("source"):
                raise GateContractError("Result source does not match accepted request")
            if manifest["tool_manifest"] != expected_request.get("tool_manifest"):
                raise GateContractError("Result tool_manifest does not match accepted request")

        # 4. Check exact member list against checks declared in manifest
        checks = manifest.get("checks", [])
        expected_names = {"manifest.json"}
        check_ids: set[str] = set()

        for c in checks:
            cid = c["id"]
            if not SAFE_SEGMENT.match(cid):
                raise GateContractError(f"Unsafe check id {cid!r}")
            if cid in check_ids:
                raise GateContractError(f"Duplicate check id in result manifest: {cid!r}")
            check_ids.add(cid)
            expected_names.add(f"logs/{cid}.log")

        actual_names = set(member_map.keys())
        if actual_names != expected_names:
            unlisted = actual_names - expected_names
            missing = expected_names - actual_names
            raise GateContractError(
                f"Result archive members do not match declared manifest. Unlisted={unlisted}, Missing={missing}",
                status=400,
                code="invalid_request",
            )

        # 5. Verify each check log with aggregate cap
        aggregate_log_bytes = 0
        for c in checks:
            cid = c["id"]
            log_name = f"logs/{cid}.log"
            log_info = member_map[log_name]

            if log_info.size != c["log_bytes"]:
                raise GateContractError(
                    f"Check {cid} log size mismatch: archive {log_info.size} != check {c['log_bytes']}"
                )
            if log_info.size > max_single_log:
                raise GateContractError(
                    f"Check {cid} log size {log_info.size} exceeds bound {max_single_log}",
                    status=413,
                    code="bounds",
                )

            aggregate_log_bytes += log_info.size
            if aggregate_log_bytes > max_aggregate_log:
                raise GateContractError(
                    f"Aggregate log bytes {aggregate_log_bytes} exceeds limit {max_aggregate_log}",
                    status=413,
                    code="bounds",
                )

            f_log = tar.extractfile(log_info)
            if f_log is None:
                raise GateContractError(f"Could not read {log_name}")
            log_bytes = f_log.read()

            if len(log_bytes) != c["log_bytes"]:
                raise GateContractError(f"Incomplete log read for {log_name}")

            actual_log_sha = hashlib.sha256(log_bytes).hexdigest()
            if actual_log_sha != c["log_sha256"]:
                raise GateContractError(
                    f"Check {cid} log SHA-256 mismatch: actual {actual_log_sha} != declared {c['log_sha256']}"
                )

            # Strict UTF-8 verification
            try:
                log_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise GateContractError(f"Check {cid} log contains invalid UTF-8: {exc}") from exc

    # 6. Semantic verification of process release & outcome
    process_release = manifest.get("process_release", {})
    if not process_release.get("confirmed"):
        raise GateContractError("Process release confirmed is required for all terminal results")
    if process_release.get("owned_processes_remaining") != 0:
        raise GateContractError("Zero owned processes remaining required for process release")

    outcome = manifest["outcome"]
    exit_code = manifest.get("exit_code")

    if outcome == "passed":
        if exit_code != 0:
            raise GateContractError(f"Passed outcome must have exit_code 0, got {exit_code}")
        if manifest.get("termination_signal") is not None:
            raise GateContractError("Passed outcome cannot have termination_signal")
        if manifest.get("reason") is not None:
            raise GateContractError("Passed outcome cannot have reason")

        # Toolchain verification for passed
        actual_tools = manifest.get("actual_toolchains", [])
        actual_ids = [t.get("id") for t in actual_tools]
        if len(actual_ids) != len(set(actual_ids)):
            raise GateContractError("Duplicate tool IDs in actual_toolchains")

        req_tools = manifest["gate"].get("required_toolchains", [])
        req_pins_by_id = {t["id"]: t for t in req_tools}
        actual_by_id = {t["id"]: t for t in actual_tools}

        if set(req_pins_by_id.keys()) != set(actual_by_id.keys()):
            raise GateContractError("actual_toolchains tool IDs must exactly match gate required_toolchains")

        for tid, req_pin in req_pins_by_id.items():
            act = actual_by_id[tid]
            if not act.get("available"):
                raise GateContractError(f"Tool {tid} must be available=True for passed outcome")
            if act.get("pin") != req_pin:
                raise GateContractError(f"Tool {tid} pin does not match required toolchain pin")

        # Exact check IDs matching (no extras) and non-null duration
        gate_check_ids = list(manifest["gate"]["check_ids"])
        manifest_check_ids = [c["id"] for c in checks]
        if gate_check_ids != manifest_check_ids:
            raise GateContractError(
                f"Passed outcome check IDs must match gate check IDs exactly in order: {manifest_check_ids} != {gate_check_ids}"
            )

        for c in checks:
            if c["outcome"] != "passed":
                raise GateContractError(f"Check {c['id']} outcome is not passed ({c['outcome']})")
            if c["exit_code"] != 0:
                raise GateContractError(f"Check {c['id']} exit code is not 0 ({c['exit_code']})")
            if c.get("duration_seconds") is None or c["duration_seconds"] < 0:
                raise GateContractError(f"Check {c['id']} must have non-null duration_seconds >= 0")
            if c.get("reason") is not None:
                raise GateContractError(f"Passed check {c['id']} must not have reason")
    else:
        if exit_code == 0:
            raise GateContractError(f"Non-passed outcome ({outcome}) cannot have exit_code 0")
        if outcome in ("cancelled", "interrupted", "not_run"):
            if manifest.get("reason") is None:
                raise GateContractError(f"Outcome {outcome} requires an explicit failure reason")

    return manifest
