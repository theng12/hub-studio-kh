"""Fail-closed production authority for developer gates.

It composes three exact local inputs: the packaged registry, the packaged runner-entry
stop-qualification receipt and the operator's frozen admission policy. Any missing, changed
or unverifiable input stays unsupported; this module never creates the policy, never enables
Developer Mode and never invents stop proof.
"""

from __future__ import annotations

import hashlib
import math
import os
import platform
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import developer_gate_contract as contract
from . import developer_gate_registry as registry_mod

RECEIPT_FORMAT = "studio.hub.gate-runner.entry-stop.qualification.v1.receipt"
PROOF_FORMAT = "studio.hub.gate-runner.entry-stop.qualification.v1"
PROOF_SCOPE = "local-host-runner-entry-only"
STOP_PROOF_REVISION = "runner-entry-stop.1"
STOP_PROOF_RELATIVE = Path("packaging/developer-gates/stop-proof.json")
POLICY_RELATIVE = Path("policy.json")
MAX_POLICY_BYTES = 64 * 1024
MAX_PROOF_BYTES = 1024 * 1024
MAX_LIMITATIONS = 64
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_FILES = {
    "developer_gate_runner": "components/hub/developer_gate_runner.py",
    "developer_image_runner": "components/hub/developer_image_runner.py",
}
_SCENARIOS = {"first-check-exit": ("failed", 7), "owned-preemption": ("interrupted", None)}


class AuthorityError(Exception):
    """One local input is missing, malformed or does not verify."""


@dataclass(frozen=True)
class LoadedAuthority:
    runner: dict[str, Any]
    gates: tuple[dict[str, Any], ...]
    tool_manifests: tuple[dict[str, Any], ...]
    stop_proof: dict[str, Any]
    policy: dict[str, Any]


def _fail(detail: str) -> None:
    raise AuthorityError(detail)


def _object(raw: bytes, where: str, max_bytes: int) -> dict[str, Any]:
    try:
        value = contract.parse_strict_json(raw, max_bytes=max_bytes)
    except contract.GateContractError as exc:
        _fail(f"{where} is not strict JSON: {exc}")
    if not isinstance(value, dict):
        _fail(f"{where} must be a JSON object")
    return value


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _digest(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value) or value == "0" * 64:
        _fail(f"{where} must be a real 64-character lowercase SHA-256")
    return value


def _bounded_file(path: Path, where: str, max_bytes: int, *,
                  owner_only: bool = False) -> bytes:
    """Open once with NOFOLLOW, then check and read the same descriptor."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        _fail(f"{where} is missing: {path} ({exc})")
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            _fail(f"{where} is not a real file: {path}")
        if owner_only and (info.st_uid != os.getuid() or info.st_mode & 0o022):
            _fail(f"{where} must be owner-controlled and not group/world writable")
        if info.st_size > max_bytes:
            _fail(f"{where} exceeds {max_bytes} bytes")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    except OSError as exc:
        _fail(f"{where} is unreadable: {exc}")
    finally:
        os.close(fd)
    if len(raw) > max_bytes:
        _fail(f"{where} exceeds {max_bytes} bytes")
    return raw


def _exact_pair(value: Any, node: str, controller: str) -> bool:
    return (isinstance(value, dict)
            and value.get("controller_id") == controller
            and value.get("node_id") == node)


def load_operator_policy(root_dir: str | Path, node: str, controller: str) -> dict[str, Any]:
    """Load the operator's frozen policy for this exact Hub controller/node pair."""
    if (not isinstance(node, str) or not contract.SAFE_SEGMENT.fullmatch(node)
            or not isinstance(controller, str)
            or not contract.SAFE_SEGMENT.fullmatch(controller)):
        _fail("the local Hub controller/node identity is not verified")
    path = Path(root_dir) / POLICY_RELATIVE
    policy = _object(_bounded_file(path, "operator policy", MAX_POLICY_BYTES,
                                   owner_only=True),
                     "operator policy", MAX_POLICY_BYTES)
    try:
        contract.validate_schema(policy, "policy")
    except contract.GateContractError as exc:
        _fail(f"operator policy is invalid: {exc}")
    if any(_exact_pair(item, node, controller) for item in policy["excluded_targets"]):
        _fail("this controller/node pair is excluded by the operator policy")
    if not any(_exact_pair(item, node, controller) for item in policy["allowed_targets"]):
        _fail("this controller/node pair is not allowed by the operator policy")
    return policy


def _source_pins(registry: registry_mod.PackagedRegistry) -> dict[str, str]:
    pins = {Path(item["path"]).as_posix(): item["sha256"] for item in registry.runner_files}
    missing = set(_SOURCE_FILES.values()) - set(pins)
    if missing:
        _fail(f"the packaged registry does not bind {sorted(missing)}")
    return {key: pins[relative] for key, relative in _SOURCE_FILES.items()}


def _check_platform(host: Any) -> None:
    if not isinstance(host, dict) or not isinstance(host.get("os"), str) \
            or not isinstance(host.get("arch"), str):
        _fail("proof host must record os and arch")
    if host["os"] != sys.platform:
        _fail(f"proof host {host['os']!r} does not match this platform {sys.platform!r}")
    machine = platform.machine()
    aliases = {"arm64": {"arm64", "aarch64"}, "aarch64": {"arm64", "aarch64"}}
    if host["arch"] not in aliases.get(machine, {machine}):
        _fail(f"proof host {host['arch']!r} does not match this machine {machine!r}")


def _check_release(release: Any, where: str) -> None:
    if not isinstance(release, dict):
        _fail(f"{where} process_release is missing")
    if release.get("confirmed") is not True:
        _fail(f"{where} process release is not confirmed")
    remaining = release.get("owned_processes_remaining")
    if isinstance(remaining, bool) or not isinstance(remaining, int) or remaining != 0:
        _fail(f"{where} still owns processes after the stop")


def _check_identities(identities: Any, where: str) -> None:
    if not isinstance(identities, list) or not identities:
        _fail(f"{where} records no checked process identity")
    for item in identities:
        if not isinstance(item, dict):
            _fail(f"{where} checked identity is not an object")
        pid = item.get("pid")
        created = item.get("create_time")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
            _fail(f"{where} checked identity has no real pid")
        if (isinstance(created, bool) or not isinstance(created, (int, float))
                or not math.isfinite(created) or created <= 0):
            _fail(f"{where} checked identity has no finite positive creation time")
        if item.get("alive_after") is not False:
            _fail(f"{where} checked identity is not recorded as gone")


def _check_scenarios(scenarios: Any) -> float:
    if not isinstance(scenarios, list) or len(scenarios) != len(_SCENARIOS):
        _fail("proof must record exactly the agreed stop scenarios")
    names: set[str] = set()
    latency: float | None = None
    required = {"name", "outcome", "exit_code", "process_release",
                "checked_identities", "group_gone", "ok"}
    for scenario in scenarios:
        if not isinstance(scenario, dict) or not required <= set(scenario):
            _fail("proof scenario is malformed")
        if not isinstance(scenario["name"], str):
            _fail("proof scenario name must be a string")
        name = scenario["name"]
        if name in names or name not in _SCENARIOS:
            _fail(f"proof scenario {name!r} is duplicate or unknown")
        names.add(name)
        outcome, exit_code = _SCENARIOS[name]
        if scenario.get("outcome") != outcome or scenario.get("exit_code") != exit_code:
            _fail(f"proof scenario {name!r} outcome/exit_code is not the agreed result")
        _check_release(scenario.get("process_release"), name)
        _check_identities(scenario.get("checked_identities"), name)
        if scenario.get("group_gone") is not True or scenario.get("ok") is not True:
            _fail(f"proof scenario {name!r} is not a confirmed clean stop")
        if name == "owned-preemption":
            value = scenario.get("stop_latency_seconds")
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0.001):
                _fail("owned-preemption stop latency must be a real positive measurement")
            latency = float(value)
    if names != set(_SCENARIOS) or latency is None:
        _fail("proof does not record both agreed stop scenarios")
    return latency


def load_packaged_stop_proof(
    root: str | Path | None = None,
    registry: registry_mod.PackagedRegistry | None = None,
) -> dict[str, Any]:
    """Verify the immutable receipt and return the exact frozen stop proof."""
    base = Path(root).resolve() if root is not None else Path(__file__).resolve().parents[2]
    receipt = _object(_bounded_file(base / STOP_PROOF_RELATIVE, "packaged stop proof",
                                    MAX_PROOF_BYTES),
                      "packaged stop proof", MAX_PROOF_BYTES)
    if set(receipt) != {"receipt_format", "proof", "proof_sha256"}:
        _fail("receipt must contain exactly receipt_format, proof and proof_sha256")
    if receipt["receipt_format"] != RECEIPT_FORMAT:
        _fail("receipt format is not " + RECEIPT_FORMAT)
    proof = receipt["proof"]
    if not isinstance(proof, dict):
        _fail("receipt proof must be an object")
    required = {"format", "scope", "created_at", "host", "sources", "scenarios", "limitations"}
    if not required <= set(proof):
        _fail(f"receipt proof must contain {sorted(required)}")
    if proof["format"] != PROOF_FORMAT or proof["scope"] != PROOF_SCOPE:
        _fail("receipt proof format/scope is not the local runner-entry qualification")
    created = proof["created_at"]
    if (isinstance(created, bool) or not isinstance(created, (int, float))
            or not math.isfinite(created) or created <= 0):
        _fail("receipt proof must record a finite positive created_at")
    actual_sha = _sha256(contract.canonical_json(proof).encode("ascii"))
    if _digest(receipt["proof_sha256"], "receipt proof_sha256") != actual_sha:
        _fail("receipt proof_sha256 does not match the canonical proof bytes")

    registry = registry or registry_mod.load_packaged_registry(root)
    pins = _source_pins(registry)
    sources = proof["sources"]
    if not isinstance(sources, dict) or set(_SOURCE_FILES) - set(sources):
        _fail("receipt proof must bind both runner source hashes")
    for key, expected in pins.items():
        if _digest(sources[key].get("sha256") if isinstance(sources[key], dict) else None,
                   f"proof source {key}") != expected:
            _fail(f"proof source {key} does not match the packaged/current runner")

    _check_platform(proof["host"])
    latency = _check_scenarios(proof["scenarios"])
    limitations = proof["limitations"]
    if (not isinstance(limitations, list) or len(limitations) > MAX_LIMITATIONS
            or not all(isinstance(item, str) for item in limitations)):
        _fail("receipt limitations must be a bounded list of strings")

    stop_proof = {"revision": STOP_PROOF_REVISION, "proof_sha256": actual_sha,
                  "stop_latency_seconds": latency}
    try:
        contract.validate_schema(stop_proof, "stopProof")
    except contract.GateContractError as exc:
        _fail(f"frozen stop proof is invalid: {exc}")
    return stop_proof


def load_packaged_authority(root_dir: str | Path, node: str, controller: str,
                            *, root: str | Path | None = None) -> LoadedAuthority:
    """Compose the verified registry, stop proof and operator policy, or fail closed."""
    registry = registry_mod.load_packaged_registry(root)
    stop_proof = load_packaged_stop_proof(root, registry=registry)
    policy = load_operator_policy(root_dir, node, controller)
    return LoadedAuthority(runner=dict(registry.runner), gates=registry.gates,
                           tool_manifests=registry.tool_manifests,
                           stop_proof=stop_proof, policy=policy)
