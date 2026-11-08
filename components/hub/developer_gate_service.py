"""Durable, authenticated node service for the frozen developer-gate pilot.

The normative pair is ``contracts/developer-gate-v1.schema.json`` and
``contracts/developer-gate-v1-wire.txt``. This module owns the lifecycle between a durably
accepted attempt and its immutable result: source verification, admission, the fixed runner,
bounded spool persistence, ACK-gated scratch removal and restart reconciliation.

Production stays explicitly unsupported until a packaged official tool/gate/dependency
registry, real tool readbacks, authoritative policy/exclusion IDs and verified stop proof
exist. Tests may inject exact trusted fixtures through the constructor; nothing here turns a
fixture into a supported production claim.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from . import developer_gate_contract as contract
from . import developer_gate_inventory as inventory_mod
from . import developer_gate_runner as gate_runner
from . import developer_gate_store as store_mod
from . import developer_gate_tools as tools_mod
from . import developer_tests

DATA_DIR_NAME = "developer-gates"
MAX_WORKSPACE_BYTES = 40_000_000_000
MAX_RESULT_SPOOL_BYTES = 2_000_000_000
DEFAULT_STOP_GRACE_SECONDS = 5.0
RECOVERY_INTERVAL_SECONDS = 30.0
ACTIVE_STATES = ("preparing_toolchains", "running", "collecting", "recovery_hold")

# Conservative closed bounds. They are limits, not measurements, and production advertises
# them only beside supported=false until the packaged registry qualifies.
DEFAULT_BOUNDS: dict[str, Any] = {
    "json_bytes": contract.ABSOLUTE_MAX_JSON_BYTES,
    "source_bytes": 64 * 1024 * 1024,
    "source_expanded_bytes": 1024 * 1024 * 1024,
    "source_files": 100_000,
    "result_bytes": 64 * 1024 * 1024,
    "log_bytes": 4 * 1024 * 1024,
    "workspace_bytes": MAX_WORKSPACE_BYTES,
    "result_spool_bytes": MAX_RESULT_SPOOL_BYTES,
    "shared_cache_bytes": 0,
    "runtime_seconds": 3600,
    "tool_prepare_seconds": 900,
    "toolchain_install_bytes": 2 * 1024 ** 3,
    "max_process_tree_rss_bytes": 8 * 1024 ** 3,
    "max_system_swap_growth_bytes": 0,
    "inventory_max_age_seconds": 300,
    "result_files": 4096,
    "workspace_enforcement": "monitored_stop",
    "sample_interval_seconds": 0.1,
}

UNSUPPORTED_REASON = {
    "code": "unsupported",
    "detail": (
        "No packaged official tool, gate and dependency registry with verified stop proof, "
        "actual tool readbacks and authoritative policy/exclusion mapping is installed; "
        "production developer gates are unsupported."
    ),
}


class _BoundedWriter:
    """Refuse a tar write before it can exceed the attempt's reserved spool bytes."""

    def __init__(self, fileobj: Any, limit: int) -> None:
        self._file = fileobj
        self._limit = limit
        self.written = 0

    def write(self, data: bytes) -> int:
        self.written += len(data)
        if self.written > self._limit:
            raise GateServiceError(413, "bounds",
                                   "the result archive exceeds its reserved spool limit")
        return self._file.write(data)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._file, name)


class GateServiceError(Exception):
    """One refusal in the frozen error shape."""

    def __init__(self, status: int, code: str, detail: str, *, acceptance: dict | None = None):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail[:2048]
        self.acceptance = acceptance


@dataclass(frozen=True)
class GateAuthority:
    """Exact trusted configuration supplied by production packaging or a focused test."""

    supported: bool
    runner: dict[str, Any]
    gates: tuple[dict[str, Any], ...] = ()
    tool_manifests: tuple[dict[str, Any], ...] = ()
    stop_proof: dict[str, Any] | None = None
    policy: dict[str, Any] | None = None
    bounds: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_BOUNDS))
    setting: dict[str, Any] | None = None
    trusted_toolchains: Mapping[str, Any] = field(default_factory=dict)
    actual_toolchains: tuple[dict[str, Any], ...] = ()
    # Trusted local configuration, never a request field: prepare exact tools, then stop
    # before any check command because the Library is not ready yet.
    tools_first_only: bool = False
    reason: dict[str, Any] | None = None


class _ToolPreparationHold(Exception):
    """Preparation may have launched a probe without confirmed release; keep lease and scratch."""


class _ToolPreparationMismatch(Exception):
    """A confirmed preparation returned facts that do not bind the accepted pin."""

    def __init__(self, detail: str, release: dict[str, Any]) -> None:
        super().__init__(detail)
        self.detail = detail
        self.release = release


class _ToolPreparationStop(Exception):
    """A service-side guard refused the next preparation step; prior probes are released."""

    def __init__(self, code: str, detail: str, release: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.release = release or {}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _runner_identity() -> dict[str, Any] | None:
    """The installed runner's actual revision and raw file hash, never a self-referential wire digest."""
    try:
        digest, _size = contract.sha256_file(Path(gate_runner.__file__).resolve())
    except (OSError, ValueError):
        return None
    return {"id": gate_runner.RUNNER_ID, "revision": gate_runner.RUNNER_REVISION,
            "sha256": digest}


def _default_policy() -> dict[str, Any]:
    """Conservative erratum policy: measured 500 GB default and no authorised target IDs yet.

    An empty allowed_targets list means no target is authorised. It is not a claim that the
    owner has no exclusions; the unknown mapping is why capabilities stays unsupported.
    """
    return {
        "format": contract.POLICY_FORMAT,
        "revision": "erratum-5ade097e5bfa",
        "minimum_internal_physical_bytes": 500_000_000_000,
        "allowed_targets": [],
        "excluded_targets": [],
    }


def unsupported_authority() -> GateAuthority:
    """Default authority: verified packaged descriptors only, never production support.

    A missing, malformed or changed registry fails closed to the plain unsupported reason
    and advertises no gates or tool manifests.
    """
    gates: tuple[dict[str, Any], ...] = ()
    tool_manifests: tuple[dict[str, Any], ...] = ()
    reason = dict(UNSUPPORTED_REASON)
    try:
        from . import developer_gate_registry as registry_mod
        registry = registry_mod.load_packaged_registry()
    except Exception:  # noqa: BLE001 - unknown packaged input stays unsupported
        pass
    else:
        gates = registry.gates
        tool_manifests = registry.tool_manifests
        reason = dict(registry.reason)
    return GateAuthority(
        supported=False,
        runner=_runner_identity() or {"id": gate_runner.RUNNER_ID,
                                      "revision": gate_runner.RUNNER_REVISION,
                                      "sha256": "0" * 64},
        gates=gates,
        tool_manifests=tool_manifests,
        policy=_default_policy(),
        bounds=dict(DEFAULT_BOUNDS),
        reason=reason,
    )


class DeveloperGateService:
    """One node's gate service. All state is durable in ``DeveloperGateStore``."""

    def __init__(
        self,
        *,
        root_dir: str | Path,
        authority: GateAuthority | None = None,
        store: store_mod.DeveloperGateStore | None = None,
        runner: Callable[..., Any] | None = None,
        metrics: Any | None = None,
        admission: Any | None = None,
        broker: Any | None = None,
        inventory_provider: Callable[[str, str], dict[str, Any] | None] | None = None,
        tool_preparer: Callable[..., Any] | None = None,
        tools_root: str | Path | None = None,
        stop_grace_seconds: float = DEFAULT_STOP_GRACE_SECONDS,
        max_workspace_bytes: int = MAX_WORKSPACE_BYTES,
        max_result_spool_bytes: int = MAX_RESULT_SPOOL_BYTES,
    ) -> None:
        self._lock = threading.RLock()
        self._recovery_lock = threading.RLock()
        self._recovery_serial = threading.Lock()
        self._recovery_running = False
        self._authority = authority or unsupported_authority()
        self._store = store or store_mod.DeveloperGateStore(
            root_dir, max_workspace_bytes=max_workspace_bytes,
            max_result_spool_bytes=max_result_spool_bytes)
        self._runner = runner or gate_runner.run_gate
        self._metrics = metrics or gate_runner.Metrics()
        self._admission = admission
        self._broker = broker
        self._inventory_provider = inventory_provider or self._default_inventory
        self._tool_preparer = tool_preparer
        self._tools_root = Path(tools_root) if tools_root is not None else None
        self._stop_grace_seconds = max(0.1, float(stop_grace_seconds))
        self._active_attempt: str | None = None
        self._worker: threading.Thread | None = None
        self._stopping = False
        self._last_recovery = 0.0
        self._inventory_cache: tuple[float, dict[str, Any]] | None = None
        self._inventory_generation = 0

    # -- identity, setting, inventory -------------------------------------
    def _admission_module(self) -> Any:
        if self._admission is not None:
            return self._admission
        from .backend import developer_test_admission
        return developer_test_admission

    def _broker_module(self) -> Any:
        if self._broker is not None:
            return self._broker
        from .backend import broker
        return broker

    def _identity(self) -> tuple[str, str]:
        try:
            node, controller = developer_tests._identities()
        except Exception as exc:  # noqa: BLE001 - unknown membership refuses
            raise GateServiceError(503, "release_unknown",
                                   f"node/controller membership is unreadable: {type(exc).__name__}") from exc
        if not isinstance(node, str) or not contract.SAFE_SEGMENT.fullmatch(node):
            raise GateServiceError(503, "release_unknown",
                                   "this Mac has no safe persisted node identity")
        if not isinstance(controller, str) or not contract.SAFE_SEGMENT.fullmatch(controller):
            raise GateServiceError(503, "release_unknown",
                                   "this Mac's controller identity is not persisted")
        return node, controller

    def _setting(self) -> dict[str, Any]:
        if self._authority.setting is not None:
            return dict(self._authority.setting)
        try:
            record, error = developer_tests._load_setting()
        except Exception:  # noqa: BLE001
            record, error = None, True
        if error or not isinstance(record, dict):
            return {"enabled": False, "verified": False, "revision": "unavailable"}
        return {"enabled": bool(record.get("enabled")),
                "verified": bool(record.get("verified")),
                "revision": str(record.get("revision") or "unavailable")}

    def _invalidate_inventory(self) -> None:
        with self._lock:
            self._inventory_generation += 1
            self._inventory_cache = None

    def _trusted_tools_manifest(self) -> tuple[dict[str, Any] | None, str | None]:
        """The configured official manifest and its raw hash; unknown preparers fail closed.

        The seam re-reads the packaged bytes, so a manifest changed after acceptance is
        unknown rather than trusted.
        """
        from . import developer_gate_tool_preparer as preparer_mod
        if not isinstance(self._tool_preparer, preparer_mod.OfficialToolPreparer):
            return None, None
        try:
            manifest, digest = self._tool_preparer.read_manifest()
        except Exception:  # noqa: BLE001 - unknown stays absent
            return None, None
        if not isinstance(manifest, Mapping) or not isinstance(digest, str):
            return None, None
        return dict(manifest), digest

    def _stored_tool_facts(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Latest schema-valid verified fact per (tool, pin) already persisted by a prep."""
        best: dict[tuple[str, str], tuple[tuple[str, str], dict[str, Any]]] = {}
        try:
            attempts = self._store.list_attempts()
        except Exception:  # noqa: BLE001 - unreadable store is unknown, never a success
            return {}
        for rec in attempts:
            facts = (rec.get("metadata") or {}).get("actual_toolchains")
            if not isinstance(facts, list):
                continue
            for raw in facts:
                if not isinstance(raw, Mapping):
                    continue
                try:
                    fact = dict(raw)
                    contract.validate_schema(fact, "toolFact")
                except contract.GateContractError:
                    continue
                if fact.get("available") is not True:
                    continue
                key = (str(fact.get("id")), contract.canonical_json(fact.get("pin")))
                rank = (str(fact.get("observed_at") or ""),
                        str(rec.get("updated_at") or ""))
                if key not in best or rank > best[key][0]:
                    best[key] = (rank, fact)
        return {key: value[1] for key, value in best.items()}

    def _retained_tool_ok(self, artifact: Mapping[str, Any],
                          max_path_length: int) -> bool:
        """Cheap proof the retained content-addressed tree and its receipt still match.

        shortcut: stat plus the small receipt only; no tree digest or version probe runs on a
        resources check-in, upgrade if inventory must ever prove file content.
        """
        root = self._tools_root
        if root is None:
            return False
        try:
            tree = root / "tools" / (
                f"{artifact['id']}-{artifact['version']}-{artifact['sha256']}")
            if tree.is_symlink() or not tree.is_dir():
                return False
            tools_mod._read_receipt(tree, artifact)
            tools_mod._verify_contained_executable(
                tree, str(artifact["executable"]), max_path_length)
        except Exception:  # noqa: BLE001 - any doubt is unavailable, never a stale success
            return False
        return True

    def _inventory_toolchains(self, manifest: Mapping[str, Any] | None,
                              manifest_sha256: str | None,
                              observed: str) -> list[dict[str, Any]]:
        pins: dict[str, dict[str, Any]] = {}
        for tool_manifest in self._authority.tool_manifests:
            tools = tool_manifest.get("tools") if isinstance(tool_manifest, Mapping) else None
            for pin in tools or []:
                if isinstance(pin, Mapping) and isinstance(pin.get("id"), str):
                    pins[pin["id"]] = dict(pin)
        if not pins:
            return []
        artifacts: dict[str, dict[str, Any]] = {}
        max_path_length = 1024
        if manifest is not None:
            limits = manifest.get("limits") or {}
            try:
                max_path_length = int(limits.get("max_path_length", max_path_length))
            except (TypeError, ValueError):
                pass
            for artifact in manifest.get("artifacts") or []:
                if isinstance(artifact, Mapping) and isinstance(artifact.get("id"), str):
                    artifacts[artifact["id"]] = dict(artifact)
        stored = self._stored_tool_facts()

        def unavailable(tool_id: str, pin: Mapping[str, Any], code: str,
                        detail: str) -> dict[str, Any]:
            return {"id": tool_id, "pin": dict(pin), "available": False,
                    "observed_at": observed, "installed_bytes": None,
                    "provenance": "owned-toolchain-readback",
                    "reason": {"code": code, "detail": detail}}

        facts: list[dict[str, Any]] = []
        for tool_id in sorted(pins):
            pin = pins[tool_id]
            fact = stored.get((tool_id, contract.canonical_json(pin)))
            if fact is None:
                facts.append(unavailable(
                    tool_id, pin, "not_prepared",
                    "no verified preparation of this configured pin is recorded on this Mac"))
                continue
            artifact = artifacts.get(tool_id)
            if manifest is None:
                facts.append(unavailable(
                    tool_id, pin, "tool_manifest_unavailable",
                    "the trusted packaged tool manifest is unavailable, so the retained "
                    "tool cannot be verified"))
                continue
            if manifest_sha256 is None or pin.get("manifest_sha256") != manifest_sha256:
                facts.append(unavailable(
                    tool_id, pin, "tool_manifest_mismatch",
                    "the configured tool pin does not bind the loaded official manifest bytes"))
                continue
            if artifact is None or artifact.get("version") != pin.get("version"):
                facts.append(unavailable(
                    tool_id, pin, "pin_unconfigured",
                    "the configured tool pin has no matching packaged official artifact"))
                continue
            if not self._retained_tool_ok(artifact, max_path_length):
                facts.append(unavailable(
                    tool_id, pin, "retained_tool_unavailable",
                    "the retained tool tree or its receipt is missing or no longer "
                    "matches the pin"))
                continue
            facts.append({
                "id": tool_id, "pin": dict(pin), "available": True,
                "observed_at": str(fact["observed_at"]),
                "installed_bytes": fact.get("installed_bytes"),
                "provenance": str(fact.get("provenance") or "owned-toolchain-readback"),
                "reason": None})
        return facts

    @staticmethod
    def _measure_cache(cache: Path) -> int:
        """Strict cache size: any unreadable entry is unknown, never a partial total."""
        total = 0

        def fail(error: OSError) -> None:
            raise error

        for current, _dirs, files in os.walk(cache, onerror=fail):
            for name in files:
                total += os.lstat(os.path.join(current, name)).st_size
        return total

    def _inventory_shared_cache(
            self, manifest: Mapping[str, Any] | None
    ) -> tuple[int | None, int | None, dict[str, Any] | None]:
        root = self._tools_root
        if root is None:
            return None, None, None
        try:
            if root.is_symlink() or not root.is_dir():
                return None, None, {
                    "code": "cache_unknown",
                    "detail": "the configured shared tool store is unavailable"}
            cache = root / "cache"
            if os.path.lexists(cache):
                if cache.is_symlink() or not cache.is_dir():
                    return None, None, {
                        "code": "cache_unknown",
                        "detail": "the shared tool cache is not a real directory"}
                measured: int | None = int(self._measure_cache(cache))
            else:
                measured = 0
        except OSError:
            return None, None, {
                "code": "cache_unknown",
                "detail": "the shared tool cache could not be measured"}
        cap: int | None = None
        if manifest is not None:
            try:
                cap = int(manifest["limits"]["download_cache_bytes"])
            except (KeyError, TypeError, ValueError):
                cap = None
        reason = None if cap is not None else {
            "code": "cache_cap_unknown",
            "detail": "the trusted packaged tool manifest exposes no shared cache cap"}
        return measured, cap, reason

    def _default_inventory(self, node: str, controller: str) -> dict[str, Any] | None:
        observed = inventory_mod._current_iso_z()
        try:
            drive = inventory_mod.probe_internal_drive(
                workspace_path=self._store.workspaces_dir)
        except Exception:  # noqa: BLE001 - unknown is null, never a guessed drive
            drive = {
                "device_id": None, "internal": None, "physical": None,
                "size_bytes": None, "source": "unknown",
                "workspace_backing_device_id": None, "backing_source": "unknown",
                "observed_at": observed,
                "reason": {"code": "unknown",
                           "detail": "internal physical drive could not be measured"},
            }
        try:
            free = int(shutil.disk_usage(self._store.workspaces_dir).free)
        except OSError:
            free = None
        configured = self._tool_preparer is not None and self._tools_root is not None
        manifest: dict[str, Any] | None = None
        manifest_sha256: str | None = None
        if configured:
            manifest, manifest_sha256 = self._trusted_tools_manifest()
        toolchains: list[dict[str, Any]] = []
        cache_bytes: int | None = None
        cache_limit: int | None = None
        cache_reason: dict[str, Any] | None = None
        if configured:
            try:
                toolchains = self._inventory_toolchains(
                    manifest, manifest_sha256, observed)
            except Exception:  # noqa: BLE001 - unknown stays absent, never a false success
                toolchains = []
            try:
                cache_bytes, cache_limit, cache_reason = self._inventory_shared_cache(manifest)
            except Exception:  # noqa: BLE001
                cache_bytes, cache_limit = None, None
                cache_reason = {"code": "cache_unknown",
                                "detail": "the shared tool cache could not be measured"}
        space = {
            "observed_at": observed,
            "workspace_bytes": self._store.active_workspace_bytes,
            "result_spool_bytes": self._store.active_spool_bytes,
            "shared_cache_bytes": cache_bytes,
            "workspace_limit_bytes": self._store.max_workspace_bytes,
            "result_spool_limit_bytes": self._store.max_result_spool_bytes,
            "shared_cache_limit_bytes": cache_limit,
            "cleanup_state": "active" if self._active_attempt else "none",
            "reason": cache_reason,
        }
        value = {
            "format": contract.INVENTORY_FORMAT,
            "node_id": node,
            "controller_id": controller,
            "observed_at": observed,
            "internal_physical_drive": drive,
            "free_bytes": free,
            "toolchains": toolchains,
            "developer_gate_space": space,
        }
        try:
            contract.validate_inventory_schema(value)
        except contract.GateContractError:
            return None
        return value

    def _inventory_value(self) -> dict[str, Any]:
        node, controller = self._identity()
        value = self._inventory_provider(node, controller)
        if not isinstance(value, dict):
            raise GateServiceError(503, "unsupported",
                                   "this Mac's developer-gate inventory is unavailable")
        try:
            contract.validate_inventory_schema(value)
        except contract.GateContractError as exc:
            raise GateServiceError(503, "integrity_error",
                                   "this Mac's developer-gate inventory is invalid") from exc
        return value

    def inventory_for_resources(self) -> dict[str, Any] | None:
        """The optional frozen inventory a resources snapshot may carry; missing stays absent."""
        now = time.time()
        with self._lock:
            cached = self._inventory_cache
            generation = self._inventory_generation
        if cached is not None and now - cached[0] < 60.0:
            return copy.deepcopy(cached[1])
        try:
            node, controller = self._identity()
            value = self._inventory_provider(node, controller)
            if not isinstance(value, dict):
                return None
            contract.validate_inventory_schema(value)
        except Exception:  # noqa: BLE001 - absent, never zero
            return None
        with self._lock:
            # A preparation or ACK during this read must not be re-cached as current.
            if generation == self._inventory_generation:
                self._inventory_cache = (now, copy.deepcopy(value))
        return value

    # -- capabilities ------------------------------------------------------
    def capabilities(self) -> dict[str, Any]:
        node, controller = self._identity()
        setting = self._setting()
        policy = self._authority.policy or _default_policy()
        caps = {
            "format": contract.CAPABILITIES_FORMAT,
            "contract": contract.CONTRACT_ID,
            "node_id": node,
            "controller_id": controller,
            "server_time": _now_iso(),
            "supported": bool(self._authority.supported),
            "reason": None if self._authority.supported else (
                self._authority.reason or dict(UNSUPPORTED_REASON)),
            "runner": dict(self._authority.runner),
            "concurrency": 1,
            "setting": setting,
            "bounds": dict(self._authority.bounds),
            "stop_proof": copy.deepcopy(self._authority.stop_proof),
            "tool_manifests": [copy.deepcopy(item) for item in self._authority.tool_manifests],
            "gates": [copy.deepcopy(item) for item in self._authority.gates],
            "inventory": self._inventory_value(),
            "admission_policy": copy.deepcopy(policy),
        }
        try:
            contract.validate_capabilities_schema(caps)
        except contract.GateContractError as exc:
            raise GateServiceError(500, "integrity_error",
                                   "this Mac's gate capabilities are invalid") from exc
        return caps

    # -- durable attempt helpers ------------------------------------------
    def _attempt(self, key: str, controller: str, node: str) -> dict[str, Any]:
        try:
            store_mod.validate_safe_id(key, "idempotency_key")
        except store_mod.GateStoreError as exc:
            raise GateServiceError(400, "invalid_request", str(exc)) from exc
        try:
            rec = self._store.get_attempt_by_key(key)
        except store_mod.GateStoreNotFoundError as exc:
            raise GateServiceError(404, "unknown_key", "no attempt exists for this key") from exc
        acc = rec.get("acceptance") or {}
        if acc.get("controller_id") != controller or acc.get("node_id") != node:
            # An unknown original key must not disclose another node's attempt.
            raise GateServiceError(404, "unknown_key", "no attempt exists for this key")
        return rec

    @staticmethod
    def _request_of(rec: dict[str, Any]) -> dict[str, Any]:
        raw = bytes.fromhex(str(rec.get("canonical_request_hex") or ""))
        value = contract.parse_strict_json(raw, max_bytes=contract.ABSOLUTE_MAX_JSON_BYTES)
        if not isinstance(value, dict):
            raise GateServiceError(500, "integrity_error",
                                   "the accepted request is not a JSON object")
        return value

    def _status(self, rec: dict[str, Any]) -> dict[str, Any]:
        inventory = self._inventory_value()
        value = self._store.get_status(rec["acceptance"]["attempt_id"], inventory=inventory)
        try:
            contract.validate_status_schema(value)
        except contract.GateContractError as exc:
            raise GateServiceError(500, "integrity_error",
                                   "the attempt status is invalid") from exc
        return value

    # -- operations --------------------------------------------------------
    def accept(self, raw: bytes, key: str, controller: str, node: str,
               actor: Mapping[str, Any], transport: Mapping[str, Any]) -> dict[str, Any]:
        try:
            store_mod.validate_safe_id(key, "idempotency_key")
        except store_mod.GateStoreError as exc:
            raise GateServiceError(400, "invalid_request", str(exc)) from exc
        # A replay returns the original immutable acceptance, even if the node is now off.
        try:
            existing = self._store.get_attempt_by_key(key)
        except store_mod.GateStoreNotFoundError:
            existing = None
        if existing is not None:
            try:
                value = contract.parse_strict_json(
                    raw, max_bytes=contract.ABSOLUTE_MAX_JSON_BYTES)
                same_request = (isinstance(value, dict)
                                and contract.canonical_json(value).encode()
                                == bytes.fromhex(str(existing.get("canonical_request_hex") or "")))
            except (contract.GateContractError, ValueError):
                same_request = False
            acceptance = existing.get("acceptance") or {}
            if not (same_request
                    and acceptance.get("controller_id") == controller
                    and acceptance.get("node_id") == node
                    and (existing.get("metadata") or {}).get("actor") == dict(actor)):
                raise GateServiceError(
                    409, "conflict",
                    "idempotency key was already used for a different attempt identity")
            return self._status(existing)
        caps = self.capabilities()
        try:
            request = contract.parse_strict_json(
                raw, max_bytes=int(caps["bounds"]["json_bytes"]))
        except contract.GateContractError as exc:
            raise GateServiceError(exc.status, exc.code, exc.detail) from exc
        if not isinstance(request, dict):
            raise GateServiceError(400, "invalid_request", "request must be a JSON object")
        try:
            contract.validate_request_semantics(
                request, caps, target_controller_id=controller, target_node_id=node)
        except contract.GateContractError as exc:
            raise GateServiceError(exc.status, exc.code, exc.detail) from exc
        # Wire section 4: physical and free admission before any source workspace is allocated.
        self._check_inventory(caps["inventory"], request, caps)
        limits = request["requested_limits"]
        metadata = {
            "actor": dict(actor),
            "transport": dict(transport),
            "original_request_hex": bytes(raw).hex(),
        }
        try:
            rec = self._store.accept_attempt(
                controller, node, key, contract.canonical_json(request).encode("utf-8"),
                requested_workspace_bytes=int(limits["workspace_bytes"]),
                reserved_spool_bytes=int(limits["result_spool_bytes"]),
                metadata=metadata)
        except store_mod.GateStoreConflictError as exc:
            raise GateServiceError(409, "conflict", str(exc)) from exc
        except store_mod.GateStoreCapacityError as exc:
            raise GateServiceError(409, "conflict", str(exc)) from exc
        except store_mod.GateStoreError as exc:
            raise GateServiceError(400, "invalid_request", str(exc)) from exc
        return self._status(rec)

    def status(self, key: str, controller: str, node: str) -> dict[str, Any]:
        return self._status(self._attempt(key, controller, node))

    def declared_source(self, key: str, controller: str, node: str) -> dict[str, Any]:
        """The pinned source descriptor for one accepted attempt (no body is read here)."""
        return copy.deepcopy(self._request_of(self._attempt(key, controller, node))["source"])

    def source(self, key: str, fileobj: Any, content_length: int | None,
               controller: str, node: str) -> dict[str, Any]:
        rec = self._attempt(key, controller, node)
        request = self._request_of(rec)
        declared = request["source"]
        metadata = rec.get("metadata") or {}
        done = metadata.get("source_completed")
        if isinstance(done, dict):
            if done.get("sha256") == declared["sha256"] and done.get("size_bytes") == declared["size_bytes"]:
                return self._status(rec)
            raise GateServiceError(409, "conflict",
                                   "a different source was already accepted for this key")
        if rec.get("state") != "waiting_source":
            raise GateServiceError(409, "conflict",
                                   f"source is not accepted in state {rec.get('state')!r}")
        if content_length is None or content_length != declared["size_bytes"]:
            raise GateServiceError(400, "invalid_request",
                                   "Content-Length must equal the pinned source size")
        bounds = self._authority.bounds
        try:
            actual_sha, actual_size = contract.sha256_stream(
                fileobj, max_bytes=int(bounds["source_bytes"]))
            fileobj.seek(0)
        except contract.GateContractError as exc:
            raise GateServiceError(exc.status, exc.code, exc.detail) from exc
        if actual_size != declared["size_bytes"] or actual_sha != declared["sha256"]:
            raise GateServiceError(400, "invalid_request",
                                   "the uploaded source does not match its pinned descriptor")
        workspace = self._store.get_workspace_path(rec["acceptance"]["attempt_id"])
        destination = workspace / "source"
        try:
            contract.validate_source_archive(fileobj, declared, bounds=bounds)
            contract.extract_source_archive(fileobj, destination, declared, bounds=bounds)
        except contract.GateContractError as exc:
            raise GateServiceError(exc.status, exc.code, exc.detail) from exc
        self._store.update_metadata(rec["acceptance"]["attempt_id"], {
            "source_completed": {
                "sha256": declared["sha256"], "size_bytes": declared["size_bytes"],
                "expanded_bytes": declared["expanded_bytes"],
                "file_count": declared["file_count"], "commit": declared["commit"],
                "completed_at": _now_iso(),
            }
        })
        rec = self._store.update_state(rec["acceptance"]["attempt_id"], "waiting_idle")
        self._maybe_start()
        return self._status(rec)

    def cancel(self, key: str, raw: bytes, controller: str, node: str) -> dict[str, Any]:
        rec = self._attempt(key, controller, node)
        try:
            value = contract.parse_strict_json(raw, max_bytes=contract.ABSOLUTE_MAX_JSON_BYTES)
            contract.validate_cancel_request_schema(value)
        except contract.GateContractError as exc:
            raise GateServiceError(exc.status, exc.code, exc.detail) from exc
        attempt_id = rec["acceptance"]["attempt_id"]
        self._store.update_metadata(attempt_id, {
            "cancel_request": value, "cancel_requested_at": _now_iso()})
        admission = self._admission_module()
        event = admission.cancel_event(attempt_id)
        if event is not None:
            event.set()
        with self._lock:
            settling = (rec.get("state") in ("waiting_source", "waiting_idle")
                        and self._active_attempt != attempt_id)
        if settling:
            self._settle_without_run(rec, self._request_of(rec), "cancelled",
                                     {"code": "cancelled",
                                      "detail": "cancellation was requested before any process started"})
        return self._status(self._store.get_attempt_by_key(key))

    def result(self, key: str, controller: str, node: str) -> tuple[Path, dict[str, Any]]:
        rec = self._attempt(key, controller, node)
        descriptor = rec.get("result")
        if not isinstance(descriptor, dict):
            raise GateServiceError(404, "unknown_key",
                                   "this attempt has no immutable result yet")
        path = self._store.get_result_path(rec["acceptance"]["attempt_id"])
        if path is None:
            raise GateServiceError(404, "unknown_key",
                                   "the result archive is no longer present")
        try:
            actual_sha, actual_size = contract.sha256_file(
                path, max_bytes=int(descriptor["size_bytes"]))
        except (OSError, contract.GateContractError) as exc:
            raise GateServiceError(500, "integrity_error",
                                   "the result archive cannot be verified") from exc
        if actual_sha != descriptor["sha256"] or actual_size != descriptor["size_bytes"]:
            raise GateServiceError(500, "integrity_error",
                                   "the result archive does not match its descriptor")
        return path, descriptor

    def ack(self, key: str, raw: bytes, controller: str, node: str) -> dict[str, Any]:
        rec = self._attempt(key, controller, node)
        try:
            value = contract.parse_strict_json(raw, max_bytes=contract.ABSOLUTE_MAX_JSON_BYTES)
            contract.validate_ack_request_schema(value)
        except contract.GateContractError as exc:
            raise GateServiceError(exc.status, exc.code, exc.detail) from exc
        attempt_id = rec["acceptance"]["attempt_id"]
        try:
            rec = self._store.ack_result(attempt_id, value["acceptance"], value["result"])
        except store_mod.GateStoreConflictError as exc:
            raise GateServiceError(409, "conflict", str(exc)) from exc
        except store_mod.GateStoreError as exc:
            raise GateServiceError(getattr(exc, "status_code", 400), "invalid_request",
                                   str(exc)) from exc
        self._invalidate_inventory()
        release = (rec.get("metadata") or {}).get("process_release")
        if isinstance(release, dict):
            try:
                rec = self._store.cleanup_workspace(
                    attempt_id, release, sample_disk_callback=self._disk_sample)
            except store_mod.GateStoreError as exc:
                raise GateServiceError(getattr(exc, "status_code", 409), "conflict",
                                       str(exc)) from exc
        self._invalidate_inventory()
        return self._status(rec)

    # -- execution ---------------------------------------------------------
    def _maybe_start(self) -> None:
        with self._lock:
            if self._stopping:
                return
            if self._active_attempt is not None:
                return
            if self._worker is not None and self._worker.is_alive():
                return
            for rec in self._store.list_attempts():
                if rec.get("state") != "waiting_idle":
                    continue
                if not isinstance((rec.get("metadata") or {}).get("source_completed"), dict):
                    continue
                if isinstance((rec.get("metadata") or {}).get("cancel_request"), dict):
                    continue
                attempt_id = rec["acceptance"]["attempt_id"]
                self._active_attempt = attempt_id
                self._worker = threading.Thread(
                    target=self._execute, args=(attempt_id,), name="developer-gate",
                    daemon=True)
                self._worker.start()
                return

    def _tool_ready(self, tool_id: str) -> bool:
        info = self._authority.trusted_toolchains.get(tool_id)
        if not isinstance(info, Mapping) or info.get("available") is not True:
            return False
        executable = info.get("executable") or info.get("path")
        if not executable:
            return False
        path = Path(str(executable))
        return path.is_file() and os.access(str(path), os.X_OK)

    @staticmethod
    def _free_need(request: Mapping[str, Any]) -> int:
        limits = request["requested_limits"]
        source = request["source"]
        return (max(int(limits["minimum_free_bytes"]), gate_runner.MINIMUM_FREE_BYTES)
                + int(source["size_bytes"]) + int(source["expanded_bytes"])
                + int(limits["workspace_bytes"]) + int(limits["result_spool_bytes"])
                + int(limits["shared_cache_bytes"]) + int(limits["toolchain_install_bytes"]))

    def _resource_safe(self, request: Mapping[str, Any]) -> bool:
        limits = request["requested_limits"]
        try:
            free = int(shutil.disk_usage(self._store.workspaces_dir).free)
            if free < self._free_need(request):
                return False
            available = int(self._metrics.available_bytes())
        except Exception:  # noqa: BLE001 - unknown resources never start
            return False
        need_memory = max(int(limits["start_min_free_memory_bytes"]),
                          int(limits["max_process_tree_rss_bytes"]))
        return available >= need_memory

    def _check_inventory(self, inventory: Mapping[str, Any], request: Mapping[str, Any],
                         caps: Mapping[str, Any]) -> None:
        """Fresh physical and free admission from the measured inventory (wire section 4)."""
        if (inventory.get("node_id") != caps.get("node_id")
                or inventory.get("controller_id") != caps.get("controller_id")):
            raise GateServiceError(409, "conflict",
                                   "the inventory does not belong to this node and controller")
        max_age = float(caps["bounds"]["inventory_max_age_seconds"])
        drive = inventory.get("internal_physical_drive") or {}
        for observed_at, what in ((inventory.get("observed_at"), "inventory"),
                                  (drive.get("observed_at"), "drive")):
            try:
                age = time.time() - datetime.strptime(
                    str(observed_at), "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=timezone.utc).timestamp()
            except (TypeError, ValueError):
                age = float("inf")
            if age > max_age or age < -max_age:
                raise GateServiceError(409, "inventory_stale",
                                       f"the {what} measurement is not fresh; nothing was launched")
        if not inventory_mod.is_internal_drive_eligible(
                drive, int(request["minimum_internal_physical_bytes"])):
            raise GateServiceError(409, "physical_ineligible",
                                   "the workspace is not on a verified internal physical drive "
                                   "of the required size; nothing was launched")
        free = inventory.get("free_bytes")
        if isinstance(free, bool) or not isinstance(free, int) or free < self._free_need(request):
            raise GateServiceError(507, "resource_unknown",
                                   "the measured free space does not cover this run's need")

    def _revalidate(self, request: Mapping[str, Any], rec: dict[str, Any]) -> None:
        """Acceptance can precede a long upload: re-pin setting, policy and physical inventory now."""
        caps = self.capabilities()
        acceptance = rec["acceptance"]
        try:
            contract.validate_request_semantics(
                dict(request), caps, target_controller_id=acceptance["controller_id"],
                target_node_id=acceptance["node_id"])
        except contract.GateContractError as exc:
            raise GateServiceError(exc.status, exc.code, exc.detail) from exc
        self._check_inventory(caps["inventory"], request, caps)

    def _disk_sample(self) -> dict[str, Any] | None:
        try:
            free = int(shutil.disk_usage(self._store.workspaces_dir).free)
            workspace = int(self._measure_dir(self._store.workspaces_dir))
        except OSError:
            return None
        return {
            "observed_at": _now_iso(), "free_bytes": max(0, free),
            "workspace_bytes": workspace,
            "result_spool_bytes": int(self._store.active_spool_bytes),
            "shared_cache_bytes": 0,
        }

    @staticmethod
    def _tar_size(payload: bytes, logs: list[bytes]) -> int:
        """Exact plain-tar size tarfile writes for these members, including record padding."""
        blocks = 512 + ((len(payload) + 511) // 512) * 512
        for data in logs:
            blocks += 512 + ((len(data) + 511) // 512) * 512
        blocks += 1024
        return ((blocks + 10239) // 10240) * 10240

    @staticmethod
    def _measure_dir(path: Path) -> int:
        total = 0
        try:
            for root, _dirs, files in os.walk(path):
                for name in files:
                    try:
                        total += os.lstat(os.path.join(root, name)).st_size
                    except OSError:
                        continue
        except OSError:
            return total
        return total

    def _prepare_tools(self, attempt_id: str, request: Mapping[str, Any],
                       admission: Any) -> tuple[dict[str, Any], list[dict[str, Any]],
                                                dict[str, Any]]:
        """Prepare every accepted tool pin through the trusted local seam.

        The shared machine lease is already held. Returned facts are checked against the
        accepted pin and kept for this attempt only; the authority is never mutated.
        """
        pins = request["tool_manifest"]["tools"]
        deadline = time.monotonic() + float(self._authority.bounds["tool_prepare_seconds"])
        executables: dict[str, Any] = {}
        facts: list[dict[str, Any]] = []
        release: dict[str, Any] | None = None
        for pin in pins:
            tool_id = str(pin["id"])
            if time.monotonic() >= deadline:
                raise _ToolPreparationStop(
                    "toolchain_timeout", "the tool preparation deadline expired before the next tool",
                    release)
            if not admission.stage_allowed(attempt_id):
                raise _ToolPreparationStop(
                    "stage_refused", "the machine is no longer allowed to stage this gate", release)
            if admission.cancel_requested(attempt_id):
                raise _ToolPreparationStop(
                    "cancelled", "cancellation was requested before the next tool preparation",
                    release)
            if not self._resource_safe(request):
                raise _ToolPreparationStop(
                    "resource_unknown",
                    "fresh disk or memory room could not be established before the next tool",
                    release)
            try:
                self._revalidate(request, self._store.get_attempt_by_id(attempt_id))
            except GateServiceError as exc:
                raise _ToolPreparationStop(exc.code, exc.detail, release) from exc
            try:
                value = self._tool_preparer(
                    tool_id,
                    attempt_root=self._store.get_workspace_path(attempt_id),
                    tools_root=self._tools_root,
                    identity_recorder=lambda identity: self._record_identity(
                        attempt_id, identity),
                    stage_allowed=lambda: admission.stage_allowed(attempt_id),
                    preempt_check=lambda: admission.cancel_requested(attempt_id),
                    resource_safe=lambda: self._resource_safe(request),
                    deadline_monotonic=deadline,
                    limits=dict(request["requested_limits"]),
                )
            except Exception as exc:  # noqa: BLE001 - an unproven release keeps the lease
                raise _ToolPreparationHold(
                    f"tool preparation for {tool_id} did not confirm process release "
                    f"({type(exc).__name__}); the lease and scratch are retained") from exc
            if not isinstance(value, Mapping):
                raise _ToolPreparationHold(
                    f"tool preparation for {tool_id} returned no exact facts")
            prepared_release = value.get("process_release")
            try:
                if not isinstance(prepared_release, Mapping):
                    raise contract.GateContractError("process release is missing")
                release = dict(prepared_release)
                contract.validate_schema(release, "processRelease")
            except contract.GateContractError as exc:
                raise _ToolPreparationHold(
                    f"tool preparation for {tool_id} did not confirm process release: {exc}") from exc
            fact = value.get("actual_toolfacts")
            executable = value.get("executable")
            try:
                if not isinstance(fact, Mapping):
                    raise contract.GateContractError("actual tool facts are missing")
                fact = dict(fact)
                contract.validate_schema(fact, "toolFact")
            except contract.GateContractError as exc:
                raise _ToolPreparationMismatch(
                    f"tool preparation facts for {tool_id} are not schema-valid: {exc}",
                    release) from exc
            if (fact.get("id") != tool_id or fact.get("pin") != pin
                    or fact.get("available") is not True):
                raise _ToolPreparationMismatch(
                    f"tool preparation facts for {tool_id} do not bind the accepted pin",
                    release)
            if not isinstance(executable, str) or not executable:
                raise _ToolPreparationMismatch(
                    f"tool preparation for {tool_id} returned no trusted executable", release)
            executables[tool_id] = {"available": True, "executable": executable,
                                    "version": str(pin.get("version"))}
            facts.append(fact)
            self._store.update_metadata(attempt_id, {
                "actual_toolchains": [dict(item) for item in facts]})
            self._invalidate_inventory()
        return executables, facts, release or {}

    def _execute(self, attempt_id: str) -> None:
        admission = self._admission_module()
        try:
            rec = self._store.get_attempt_by_id(attempt_id)
            request = self._request_of(rec)
            if isinstance((rec.get("metadata") or {}).get("cancel_request"), dict):
                self._settle_without_run(
                    rec, request, "cancelled",
                    {"code": "cancelled",
                     "detail": "cancellation was requested before any process started"})
                return
            if not self._authority.supported:
                self._settle_without_run(rec, request, "failed", dict(UNSUPPORTED_REASON))
                return
            if not self._resource_safe(request):
                self._settle_without_run(
                    rec, request, "failed",
                    {"code": "resource_unknown",
                     "detail": "fresh disk or memory room could not be established before launch"})
                return
            required = [str(tool["id"]) for tool in request["tool_manifest"]["tools"]]
            missing = [tool for tool in required if not self._tool_ready(tool)]
            rec = self._store.update_state(attempt_id, "preparing_toolchains")
            try:
                self._revalidate(request, rec)
            except GateServiceError as exc:
                self._settle_without_run(rec, request, "failed",
                                         {"code": exc.code, "detail": exc.detail})
                return
            if (self._authority.tools_first_only
                    and (self._tool_preparer is None or self._tools_root is None)):
                # Tools-first never trusts a stale ready tool: it needs a fresh probe.
                self._settle_without_run(
                    rec, request, "not_run",
                    {"code": "toolchain_unavailable",
                     "detail": "tools-first mode requires fresh trusted preparation, but no "
                               "preparation provider is configured; no check command was run"})
                return
            if self._tool_preparer is None or self._tools_root is None:
                if missing:
                    # No trusted preparation seam: settle honestly before any lease or process.
                    self._settle_without_run(
                        rec, request, "not_run",
                        {"code": "toolchain_unavailable",
                         "detail": "no trusted packaged tool preparation is configured for "
                                   + ", ".join(sorted(set(missing))) + "; no process was launched"})
                    return
                prepared_tools = dict(self._authority.trusted_toolchains)
                actual_facts: tuple[dict[str, Any], ...] = tuple(
                    self._authority.actual_toolchains)
                prep_release: dict[str, Any] = {}
            # The disk baseline and the one shared machine lease both precede preparation.
            before = self._disk_sample()
            try:
                admission.reserve_app_gate(attempt_id, machine="local", resource_safe=True)
            except Exception as exc:  # noqa: BLE001 - admission refusal is a plain reason
                code = str(getattr(exc, "code", "resource_unknown") or "resource_unknown")
                detail = str(getattr(exc, "detail", "admission was refused before launch"))
                self._settle_without_run(rec, request, "failed", {"code": code, "detail": detail})
                return
            if self._tool_preparer is not None and self._tools_root is not None:
                try:
                    prepared_tools, facts, prep_release = self._prepare_tools(
                        attempt_id, request, admission)
                except _ToolPreparationMismatch as exc:
                    rec = self._store.get_attempt_by_id(attempt_id)
                    self._settle_without_run(
                        rec, request, "failed",
                        {"code": "toolchain_mismatch", "detail": exc.detail},
                        release_admission=True, process_release=exc.release)
                    return
                except _ToolPreparationStop as exc:
                    rec = self._store.get_attempt_by_id(attempt_id)
                    self._settle_without_run(
                        rec, request,
                        "cancelled" if exc.code == "cancelled" else "failed",
                        {"code": exc.code, "detail": exc.detail},
                        release_admission=True, process_release=exc.release or None)
                    return
                except _ToolPreparationHold as exc:
                    self._hold(attempt_id, str(exc))
                    return
                except Exception:  # noqa: BLE001 - unknown release retains lease and scratch
                    self._hold(
                        attempt_id,
                        "tool preparation did not confirm an exact process release; the "
                        "physical hold and scratch are retained")
                    return
                actual_facts = tuple(facts)
                if admission.cancel_requested(attempt_id):
                    self._settle_without_run(
                        rec, request, "cancelled",
                        {"code": "cancelled",
                         "detail": "cancellation was requested before any check command started"},
                        release_admission=True, process_release=prep_release,
                        actual_toolchains=actual_facts)
                    return
                if self._authority.tools_first_only:
                    self._settle_without_run(
                        rec, request, "not_run",
                        {"code": "library_wait",
                         "detail": "exact tools are prepared; the gate is waiting for the "
                                   "Library, so no check command was run"},
                        release_admission=True, process_release=prep_release,
                        actual_toolchains=actual_facts, before=before)
                    return
            # The service, not the provider, owns the last gate before any check command.
            if not admission.stage_allowed(attempt_id):
                self._settle_without_run(
                    rec, request, "failed",
                    {"code": "stage_refused",
                     "detail": "the machine is no longer allowed to stage this gate"},
                    release_admission=True, process_release=prep_release or None,
                    actual_toolchains=actual_facts)
                return
            if admission.cancel_requested(attempt_id):
                self._settle_without_run(
                    rec, request, "cancelled",
                    {"code": "cancelled",
                     "detail": "cancellation was requested before any check command started"},
                    release_admission=True, process_release=prep_release or None,
                    actual_toolchains=actual_facts)
                return
            if not self._resource_safe(request):
                self._settle_without_run(
                    rec, request, "failed",
                    {"code": "resource_unknown",
                     "detail": "fresh disk or memory room could not be established before the "
                               "check command"},
                    release_admission=True, process_release=prep_release or None,
                    actual_toolchains=actual_facts)
                return
            try:
                self._revalidate(request, rec)
            except GateServiceError as exc:
                self._settle_without_run(
                    rec, request, "failed", {"code": exc.code, "detail": exc.detail},
                    release_admission=True, process_release=prep_release or None,
                    actual_toolchains=actual_facts)
                return
            rec = self._store.update_state(attempt_id, "waiting_idle")
            rec = self._store.update_state(attempt_id, "running")
            try:
                result = self._runner(
                    workspace_dir=self._store.get_workspace_path(attempt_id),
                    source_dir=self._store.get_workspace_path(attempt_id) / "source",
                    trusted_toolchains=prepared_tools,
                    stage_allowed=lambda: admission.stage_allowed(attempt_id),
                    preempt_check=lambda: admission.cancel_requested(attempt_id),
                    identity_recorder=lambda identity: self._record_identity(attempt_id, identity),
                    limits=dict(request["requested_limits"]),
                    metrics=self._metrics,
                    before=before,
                    secrets=(),
                )
            except Exception as exc:  # noqa: BLE001 - unknown release is never terminal
                self._handle_runner_failure(attempt_id, rec, request, exc)
                return
            release = result.process_release if isinstance(result.process_release, dict) else {}
            confirmed = release.get("confirmed") is True and release.get("owned_processes_remaining") == 0
            self._store.update_metadata(attempt_id, {"process_release": dict(release)})
            try:
                released = admission.release(attempt_id, confirmed=bool(confirmed)) is True
            except Exception:  # noqa: BLE001
                released = False
            if not released:
                confirmed = False
            rec = self._store.update_state(attempt_id, "collecting")
            if not confirmed:
                self._store.update_state(
                    attempt_id, "recovery_hold",
                    reason={"code": "release_unknown",
                            "detail": "the owned process tree is not confirmed released"})
                return
            try:
                self._persist_result(rec, request, result, actual_toolchains=actual_facts)
            except Exception:  # noqa: BLE001 - evidence must not be claimed if it cannot be built
                self._store.update_state(
                    attempt_id, "recovery_hold",
                    reason={"code": "result_unavailable",
                            "detail": "the immutable result archive could not be built"})
                return
            outcome = str(result.outcome)
            if outcome not in ("passed", "failed", "cancelled", "interrupted", "not_run"):
                outcome = "failed"
            self._store.update_state(
                attempt_id, outcome, reason=result.reason,
                resource_release_confirmed=True)
        finally:
            with self._lock:
                if self._active_attempt == attempt_id:
                    self._active_attempt = None
            self._maybe_start()

    def _handle_runner_failure(self, attempt_id: str, rec: dict[str, Any],
                               request: dict[str, Any], exc: BaseException) -> None:
        admission = self._admission_module()
        try:
            # The identity recorder writes after this record was read; reload the durable copy.
            rec = self._store.get_attempt_by_id(attempt_id)
        except Exception:  # noqa: BLE001 - unknown identity is retained as a hold
            rec = {}
        identity = (rec.get("metadata") or {}).get("child_identity")
        proof = None
        if isinstance(identity, dict):
            try:
                proof = gate_runner.stop_exact_identity(
                    identity, metrics=self._metrics, clock=time.monotonic,
                    grace=self._stop_grace_seconds)
            except Exception:  # noqa: BLE001
                proof = None
        confirmed = bool(proof and proof.confirmed)
        try:
            released = admission.release(attempt_id, confirmed=bool(confirmed)) is True
        except Exception:  # noqa: BLE001
            released = False
        if not released:
            confirmed = False
        if not confirmed:
            self._store.update_state(
                attempt_id, "recovery_hold",
                reason={"code": "release_unknown",
                        "detail": "the runner failed and its owned process tree is not confirmed released"})
            return
        release = {"confirmed": True, "observed_at": _now_iso(),
                   "owned_processes_remaining": 0,
                   "proof": "owned-process-identities-exited"}
        self._store.update_metadata(attempt_id, {"process_release": release})
        try:
            self._settle_without_run(
                rec, request, "failed",
                {"code": "internal_error",
                 "detail": f"the fixed runner failed before a result: {type(exc).__name__}"},
                release_admission=False, process_release=release)
        except Exception:  # noqa: BLE001
            self._store.update_state(
                attempt_id, "recovery_hold",
                reason={"code": "result_unavailable",
                        "detail": "the failure result archive could not be built"})

    def _record_identity(self, attempt_id: str, identity: dict[str, Any]) -> None:
        self._store.update_metadata(attempt_id, {
            "child_identity": dict(identity), "child_identity_at": _now_iso()})

    def _settle_without_run(self, rec: dict[str, Any], request: dict[str, Any],
                            outcome: str, reason: dict[str, Any], *,
                            release_admission: bool = False,
                            process_release: dict[str, Any] | None = None,
                            actual_toolchains: tuple[dict[str, Any], ...] | None = None,
                            before: dict[str, Any] | None = None) -> None:
        attempt_id = rec["acceptance"]["attempt_id"]
        release = process_release or {
            "confirmed": True, "observed_at": _now_iso(),
            "owned_processes_remaining": 0, "proof": "never_started"}
        if release_admission:
            try:
                released = self._admission_module().release(attempt_id, confirmed=True) is True
            except Exception:  # noqa: BLE001
                released = False
            if not released:
                self._hold(attempt_id, "the physical hold was not confirmed released")
                return
        self._store.update_metadata(attempt_id, {"process_release": dict(release)})
        result = gate_runner.GateRunResult(
            outcome=outcome, exit_code=None, termination_signal=None,
            reason=dict(reason), checks=[], check_logs={},
            process_release=dict(release), started_at=None, finished_at=_now_iso(),
            scope=gate_runner.SCOPE,
            peak_workspace_bytes=self._measure_dir(self._store.workspaces_dir),
            before=before)
        self._persist_result(rec, request, result, actual_toolchains=actual_toolchains)
        self._store.update_state(attempt_id, outcome, reason=dict(reason),
                                 resource_release_confirmed=True)

    def _persist_result(self, rec: dict[str, Any], request: dict[str, Any],
                        result: Any, *,
                        actual_toolchains: tuple[dict[str, Any], ...] | None = None,
                        ) -> dict[str, Any]:
        inventory = self._inventory_value()
        if actual_toolchains is None:
            stored = (rec.get("metadata") or {}).get("actual_toolchains")
            actual_toolchains = tuple(stored) if isinstance(stored, list) else ()
        facts = actual_toolchains or self._authority.actual_toolchains
        manifest = {
            "format": contract.RESULT_FORMAT,
            "acceptance": copy.deepcopy(rec["acceptance"]),
            "runner": copy.deepcopy(request["runner"]),
            "gate": copy.deepcopy(request["gate"]),
            "source": copy.deepcopy(request["source"]),
            "tool_manifest": copy.deepcopy(request["tool_manifest"]),
            "actual_toolchains": [copy.deepcopy(item) for item in facts],
            "outcome": result.outcome,
            "exit_code": result.exit_code,
            "termination_signal": result.termination_signal,
            "reason": copy.deepcopy(result.reason),
            "checks": copy.deepcopy(result.checks),
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "process_release": copy.deepcopy(result.process_release),
            "internal_physical_drive": copy.deepcopy(inventory["internal_physical_drive"]),
            "before": copy.deepcopy(result.before),
            "peak_workspace_bytes": result.peak_workspace_bytes,
        }
        try:
            contract.validate_result_manifest_schema(manifest)
        except contract.GateContractError as exc:
            raise GateServiceError(500, "integrity_error",
                                   "the result manifest is invalid") from exc
        payload = contract.canonical_json(manifest).encode("utf-8")
        logs = [(cid, bytes(raw_log))
                for cid, raw_log in sorted((result.check_logs or {}).items())]
        bounds = self._authority.bounds
        limit = min(int(bounds["result_bytes"]), int(rec["reserved_spool_bytes"]),
                    int(request["requested_limits"]["result_spool_bytes"]))
        if (len(payload) > int(bounds["json_bytes"])
                or len(logs) + 1 > int(bounds["result_files"])
                or any(len(data) > int(bounds["log_bytes"]) for _cid, data in logs)):
            raise GateServiceError(413, "bounds", "the result evidence exceeds a node bound")
        needed = self._tar_size(payload, [data for _cid, data in logs])
        if needed > limit:
            raise GateServiceError(413, "bounds",
                                   "the result archive exceeds its reserved spool limit")
        spool_dir = self._store.spool_dir / rec["acceptance"]["attempt_id"]
        spool_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        # The temp archive and the store's persisted copy coexist; both need live room.
        floor = (max(int(request["requested_limits"]["minimum_free_bytes"]),
                     gate_runner.MINIMUM_FREE_BYTES) + 2 * needed)
        try:
            if int(shutil.disk_usage(spool_dir).free) < floor:
                raise GateServiceError(507, "resource_unknown",
                                       "the result spool volume has no free room above the "
                                       "live free floor")
        except OSError as exc:
            raise GateServiceError(507, "resource_unknown",
                                   "the result spool volume cannot be measured") from exc
        archive = tempfile.TemporaryFile(dir=spool_dir)
        try:
            writer = _BoundedWriter(archive, limit)
            with tarfile.open(fileobj=writer, mode="w") as tar:
                info = tarfile.TarInfo("manifest.json")
                info.size = len(payload)
                info.mode = 0o600
                tar.addfile(info, io.BytesIO(payload))
                for cid, data in logs:
                    log_info = tarfile.TarInfo(f"logs/{cid}.log")
                    log_info.size = len(data)
                    log_info.mode = 0o600
                    tar.addfile(log_info, io.BytesIO(data))
            archive.seek(0)
            try:
                contract.validate_result_archive(
                    archive, expected_acceptance=rec["acceptance"],
                    bounds=self._authority.bounds)
            except contract.GateContractError as exc:
                raise GateServiceError(500, "integrity_error",
                                       "the result archive is invalid") from exc
            archive.seek(0)
            return self._store.persist_result_archive(
                rec["acceptance"]["attempt_id"], archive)
        finally:
            archive.close()

    # -- restart and runtime recovery --------------------------------------
    def recover(self) -> dict[str, Any]:
        """Startup pass: settle never-started attempts, restore each durable physical hold."""
        with self._recovery_serial:
            return self._recover(startup=True)

    def recover_runtime(self) -> dict[str, Any]:
        """Runtime pass: only records with no live worker; waiting uploads are never touched."""
        with self._recovery_serial:
            return self._recover(startup=False)

    def _recover(self, *, startup: bool) -> dict[str, Any]:
        from .backend import developer_test_admission as admission
        broker = self._broker_module()
        with self._lock:
            active_attempt = self._active_attempt
        result: dict[str, Any] = {"interrupted": [], "recovery_hold": [], "not_run": []}
        for rec in self._store.list_attempts():
            attempt_id = rec["acceptance"]["attempt_id"]
            if attempt_id == active_attempt:
                continue
            state = rec.get("state")
            if startup and state in ("waiting_source", "waiting_idle"):
                reason = {"code": "restarted", "detail": (
                    "the Hub restarted before source upload; the attempt was not rerun"
                    if state == "waiting_source" else
                    "the Hub restarted before any process started; the attempt was not rerun")}
                try:
                    self._settle_without_run(rec, self._request_of(rec), "not_run", reason)
                    result["not_run"].append(attempt_id)
                except Exception:  # noqa: BLE001
                    self._hold(attempt_id, "restarted; the attempt could not be settled")
                    result["recovery_hold"].append(attempt_id)
                continue
            if state not in ACTIVE_STATES:
                continue
            metadata = rec.get("metadata") or {}
            identity = metadata.get("child_identity")
            owner = admission.owner_for(attempt_id)
            try:
                broker.restore_external_machine("local", owner)
            except Exception:  # noqa: BLE001 - another owner keeps this unknown
                self._hold(attempt_id, "the physical hold could not be restored")
                result["recovery_hold"].append(attempt_id)
                continue
            if not isinstance(identity, dict):
                self._hold(attempt_id,
                           "no persisted child identity exists; the physical hold is retained")
                result["recovery_hold"].append(attempt_id)
                continue
            try:
                proof = gate_runner.stop_exact_identity(
                    identity, metrics=self._metrics, clock=time.monotonic,
                    grace=self._stop_grace_seconds)
            except Exception:  # noqa: BLE001
                proof = None
            if not proof or not proof.confirmed:
                self._hold(attempt_id, "the exact owned process identity is not confirmed released")
                result["recovery_hold"].append(attempt_id)
                continue
            broker.release_external_machine("local", owner)
            release = {"confirmed": True, "observed_at": _now_iso(),
                       "owned_processes_remaining": 0,
                       "proof": "owned-process-identities-exited"}
            self._store.update_metadata(attempt_id, {"process_release": release})
            try:
                settled = self._store.reconcile_orphan(
                    attempt_id, release, outcome="interrupted",
                    reason={"code": "interrupted",
                            "detail": "the Hub restarted while this attempt was active; the "
                                      "exact owned process is released and it was not rerun"},
                    sample_disk_callback=self._disk_sample)
            except Exception:  # noqa: BLE001
                self._hold(attempt_id, "orphan reconciliation failed")
                result["recovery_hold"].append(attempt_id)
                continue
            if settled.get("state") == "recovery_hold":
                result["recovery_hold"].append(attempt_id)
            else:
                result["interrupted"].append(attempt_id)
        return result

    def _hold(self, attempt_id: str, detail: str) -> None:
        try:
            self._store.update_state(
                attempt_id, "recovery_hold",
                reason={"code": "recovery_hold", "detail": detail})
        except Exception:  # noqa: BLE001 - a hold that cannot be written stays unclaimed
            pass

    def recover_if_due(self) -> None:
        """Run one bounded runtime pass in the background, never overlapping another."""
        now = time.monotonic()
        with self._recovery_lock:
            if self._recovery_running:
                return
            if now - self._last_recovery < RECOVERY_INTERVAL_SECONDS:
                return
            self._last_recovery = now
            self._recovery_running = True
        thread = threading.Thread(target=self._recover_bounded, name="developer-gate-recovery",
                                  daemon=True)
        thread.start()

    def _recover_bounded(self) -> None:
        try:
            self.recover_runtime()
        except Exception:  # noqa: BLE001 - a failed pass retains every unknown hold
            return
        finally:
            with self._recovery_lock:
                self._recovery_running = False

    def shutdown(self) -> dict[str, Any]:
        """Fence new starts and ask the active owned process to stop; release stays unproven."""
        with self._lock:
            self._stopping = True
            attempt_id = self._active_attempt
        if attempt_id is not None:
            try:
                event = self._admission_module().cancel_event(attempt_id)
                if event is not None:
                    event.set()
            except Exception:  # noqa: BLE001
                pass
        return {"stopping": True, "active_attempt": attempt_id}

    def close(self) -> None:
        self._store.close()


# ---------------------------------------------------------------------------
# Process-wide configuration used by the packaged Hub.
# ---------------------------------------------------------------------------
_SERVICE: DeveloperGateService | None = None
_BLOCK: dict[str, Any] | None = None
_LOCK = threading.RLock()


def configure(*, root_dir: str | Path | None = None, authority: GateAuthority | None = None,
              runner: Callable[..., Any] | None = None, metrics: Any | None = None,
              admission: Any | None = None,
              inventory_provider: Callable[[str, str], dict[str, Any] | None] | None = None,
              tool_preparer: Callable[..., Any] | None = None,
              tools_root: str | Path | None = None,
              max_workspace_bytes: int = MAX_WORKSPACE_BYTES,
              max_result_spool_bytes: int = MAX_RESULT_SPOOL_BYTES) -> dict[str, Any]:
    """Build the node service. No attempt is read, started or recovered here."""
    global _SERVICE, _BLOCK
    with _LOCK:
        if _SERVICE is not None:
            return {"configured": True}
        try:
            if root_dir is None:
                from .standalone_config import data_dir
                root_dir = data_dir() / DATA_DIR_NAME
            _SERVICE = DeveloperGateService(
                root_dir=root_dir, authority=authority, runner=runner, metrics=metrics,
                admission=admission, inventory_provider=inventory_provider,
                tool_preparer=tool_preparer, tools_root=tools_root,
                max_workspace_bytes=max_workspace_bytes,
                max_result_spool_bytes=max_result_spool_bytes)
            _BLOCK = None
            return {"configured": True, "supported": _SERVICE._authority.supported}
        except Exception as exc:  # noqa: BLE001 - optional work never blocks Hub startup
            _SERVICE = None
            _BLOCK = {"code": "unsupported",
                      "detail": f"developer gate service is unavailable: {type(exc).__name__}"}
            return {"configured": False, "reason": _BLOCK}


def get_service() -> DeveloperGateService:
    with _LOCK:
        service = _SERVICE
    if service is None:
        raise GateServiceError(503, "unsupported",
                               (_BLOCK or UNSUPPORTED_REASON)["detail"])
    return service


def recover() -> dict[str, Any]:
    try:
        return get_service().recover()
    except GateServiceError as exc:
        return {"interrupted": [], "recovery_hold": [], "not_run": [],
                "error": exc.detail}


def recover_if_due() -> None:
    with _LOCK:
        service = _SERVICE
    if service is not None:
        service.recover_if_due()


def inventory_for_resources() -> dict[str, Any] | None:
    with _LOCK:
        service = _SERVICE
    return None if service is None else service.inventory_for_resources()


def shutdown() -> dict[str, Any]:
    with _LOCK:
        service = _SERVICE
    return {"stopping": False} if service is None else service.shutdown()


def _reset() -> None:
    """Test-only: forget the built service."""
    global _SERVICE, _BLOCK
    with _LOCK:
        if _SERVICE is not None:
            try:
                _SERVICE.close()
            except Exception:  # noqa: BLE001
                pass
        _SERVICE = None
        _BLOCK = None
