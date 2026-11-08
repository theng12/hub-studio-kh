"""Strict loader for the packaged Library portable developer-gate fixture.

The loader verifies exact packaged bytes against the registry's raw SHA-256 pins and
returns descriptors only. It never enables production dispatch, never invents stop
proof or measurements, and fails closed on missing or changed data.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import developer_gate_contract as contract
from . import developer_gate_runner as gate_runner

REGISTRY_FORMAT = "studio.developer-gate.registry.v1"
REGISTRY_RELATIVE = Path("packaging/developer-gates/library-portable.registry.json")
MAX_DATA_BYTES = contract.ABSOLUTE_MAX_JSON_BYTES
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ZERO_SHA256 = "0" * 64

UNSUPPORTED_REASON = {
    "code": "unsupported",
    "detail": (
        "Packaged Library portable fixture descriptors verified; production dispatch "
        "remains unsupported until verified stop proof, owner admission policy and fresh "
        "measurements exist."
    ),
}


class RegistryError(Exception):
    """A packaged input is missing, malformed or does not match its pinned identity."""


@dataclass(frozen=True)
class PackagedRegistry:
    runner: dict[str, Any]
    gates: tuple[dict[str, Any], ...]
    tool_manifests: tuple[dict[str, Any], ...]
    supported: bool = False
    reason: dict[str, str] = field(default_factory=lambda: dict(UNSUPPORTED_REASON))


def _fail(detail: str) -> None:
    raise RegistryError(detail)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _check_sha256(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value) or value == _ZERO_SHA256:
        _fail(f"{where} must be a real 64-character lowercase SHA-256")
    return value


def _read_bytes(path: Path, where: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            _fail(f"{where} is missing: {path}")
        with path.open("rb") as handle:
            raw = handle.read(MAX_DATA_BYTES + 1)
    except OSError as exc:
        _fail(f"{where} is unreadable: {exc}")
    if len(raw) > MAX_DATA_BYTES:
        _fail(f"{where} exceeds {MAX_DATA_BYTES} bytes")
    return raw


def _strict_object(raw: bytes, where: str) -> dict[str, Any]:
    try:
        value = contract.parse_strict_json(raw, max_bytes=MAX_DATA_BYTES)
    except contract.GateContractError as exc:
        _fail(f"{where} is not strict JSON: {exc}")
    if not isinstance(value, dict):
        _fail(f"{where} must be a JSON object")
    return value


def _expect_keys(value: Any, keys: set[str], where: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        _fail(f"{where} must contain exactly {sorted(keys)}")


def _file_ref(entry: Any, where: str) -> tuple[str, str]:
    _expect_keys(entry, {"file", "sha256"}, where)
    name = entry["file"]
    if not isinstance(name, str) or not _SAFE_FILE.fullmatch(name):
        _fail(f"{where}.file must be one packaged file name")
    return name, _check_sha256(entry["sha256"], f"{where}.sha256")


def load_packaged_registry(root: str | Path | None = None) -> PackagedRegistry:
    """Load and verify the packaged fixture registry, or fail closed.

    ``root`` exists for a staged copy of the same layout; it never changes what is
    trusted, only where the exact packaged bytes are read from.
    """
    base = Path(root).resolve() if root is not None else Path(__file__).resolve().parents[2]
    registry_path = base / REGISTRY_RELATIVE
    registry = _strict_object(_read_bytes(registry_path, "packaged registry"),
                              "packaged registry")
    _expect_keys(registry, {"format", "revision", "gate", "command_fixture",
                            "dependency_manifest", "tool_manifest", "runner"},
                 "packaged registry")
    if registry["format"] != REGISTRY_FORMAT:
        _fail("packaged registry format is not " + REGISTRY_FORMAT)
    if not isinstance(registry["revision"], str) or not registry["revision"]:
        _fail("packaged registry revision must be a non-empty string")

    raw_by_key: dict[str, bytes] = {}
    refs: dict[str, tuple[Path, str]] = {}
    for key in ("gate", "command_fixture", "dependency_manifest", "tool_manifest"):
        name, digest = _file_ref(registry[key], key)
        refs[key] = (registry_path.parent / name, digest)
    for key, (path, digest) in refs.items():
        raw = _read_bytes(path, key)
        actual = _sha256(raw)
        if actual != digest:
            _fail(f"{key} SHA-256 mismatch: {actual} != {digest}")
        raw_by_key[key] = raw

    gate_def = _strict_object(raw_by_key["gate"], "gate definition")
    if "sha256" in gate_def:
        _fail("gate definition must not contain its own sha256")
    _expect_keys(gate_def, {"id", "revision", "source", "command_fixture_sha256",
                            "dependency_manifest_sha256", "tool_manifest", "limits",
                            "budget_provenance", "check_ids"}, "gate definition")
    tool_manifest = gate_def["tool_manifest"]
    _expect_keys(tool_manifest, {"format", "id", "revision", "sha256", "tools",
                                 "provenance"}, "tool manifest")
    pins = tool_manifest["tools"]
    if not isinstance(pins, list):
        _fail("tool manifest tools must be an array")
    gate = dict(gate_def)
    gate["required_toolchains"] = [dict(pin) for pin in pins]
    gate["sha256"] = refs["gate"][1]
    try:
        contract.validate_schema(gate, "gate")
        contract.validate_schema(tool_manifest, "toolManifest")
    except contract.GateContractError as exc:
        _fail(f"packaged gate descriptor is invalid: {exc}")

    if gate["command_fixture_sha256"] != refs["command_fixture"][1]:
        _fail("gate command fixture hash does not match the packaged fixture")
    if gate["dependency_manifest_sha256"] != refs["dependency_manifest"][1]:
        _fail("gate dependency manifest hash does not match the packaged manifest")
    if tool_manifest["sha256"] != refs["tool_manifest"][1]:
        _fail("tool manifest hash does not match the packaged tool manifest")
    tool_ids = [pin["id"] for pin in pins]
    if len(tool_ids) != len(set(tool_ids)):
        _fail("duplicate tool identifiers in the packaged gate")
    for pin in pins:
        if pin["manifest_sha256"] != refs["tool_manifest"][1]:
            _fail(f"tool {pin['id']} does not bind the packaged tool manifest")
    check_ids = gate["check_ids"]
    if len(check_ids) != len(set(check_ids)):
        _fail("duplicate check identifiers in the packaged gate")

    fixture = _strict_object(raw_by_key["command_fixture"], "command fixture")
    if fixture.get("fixtureOnly") is not True or fixture.get("dispatchPerformed") is not False:
        _fail("command input must stay test-only with no dispatch")
    if fixture.get("scope") != "portable-mocked-checks-only":
        _fail("command fixture scope is not the approved portable subset")
    if fixture.get("sourceCommit") != gate["source"]["commit"]:
        _fail("command fixture source commit does not match the gate source")
    if fixture.get("archiveSHA256") != gate["source"]["sha256"]:
        _fail("command fixture source archive hash does not match the gate source")
    if fixture.get("archiveBytes") != gate["source"]["size_bytes"]:
        _fail("command fixture source archive size does not match the gate source")
    commands = fixture.get("commands")
    if not isinstance(commands, list) or not all(isinstance(item, dict) for item in commands):
        _fail("command fixture commands must be an array of objects")
    if [item.get("id") for item in commands] != check_ids:
        _fail("command fixture command IDs do not match the gate check IDs")
    requirements = fixture.get("executionRequirements")
    if not isinstance(requirements, dict):
        _fail("command fixture execution requirements are missing")
    if requirements.get("minimumDiskBytes") != gate["limits"]["workspace_bytes"]:
        _fail("fixture disk minimum and gate workspace limit disagree")
    if requirements.get("minimumMemoryBytes") != gate["limits"]["start_min_free_memory_bytes"]:
        _fail("fixture memory minimum and gate start memory limit disagree")
    if fixture.get("testedToolchains") != {pin["id"]: pin["version"] for pin in pins}:
        _fail("command fixture tool versions do not match the packaged tool pins")
    if fixture.get("thirdPartyDependencyDownloadsRequired") is not False:
        _fail("command fixture must not require third-party dependency downloads")

    dependency = _strict_object(raw_by_key["dependency_manifest"], "dependency manifest")
    if dependency.get("sourceCommit") != gate["source"]["commit"]:
        _fail("dependency manifest source commit does not match the gate source")
    if dependency.get("sourceArchiveSHA256") != gate["source"]["sha256"]:
        _fail("dependency manifest source archive hash does not match the gate source")
    if dependency.get("commandsSHA256") != gate["command_fixture_sha256"]:
        _fail("dependency manifest fixture hash does not match the gate fixture")
    for flag in ("dispatchPerformed", "gatePerformed", "sourceChanged"):
        if dependency.get(flag) is not False:
            _fail(f"dependency manifest {flag} must be false")

    official = _strict_object(raw_by_key["tool_manifest"], "official tools manifest")
    if official.get("format") != "studio.developer-gate.official-tools.v1":
        _fail("official tools manifest format is not the packaged archive format")
    if official.get("production_installer_qualified") is not False:
        _fail("official tools manifest must not claim production installation")
    official_limits = official.get("limits")
    if not isinstance(official_limits, dict):
        _fail("official tools manifest limits are missing")
    if gate["limits"]["shared_cache_bytes"] != official_limits.get("download_cache_bytes"):
        _fail("gate shared cache limit does not match the packaged download cache cap")
    artifacts = official.get("artifacts")
    if not isinstance(artifacts, list):
        _fail("official tools manifest artifacts must be an array")
    by_id = {item["id"]: item for item in artifacts
             if isinstance(item, dict) and isinstance(item.get("id"), str)}
    for pin in pins:
        artifact = by_id.get(pin["id"])
        if artifact is None or artifact.get("version") != pin["version"]:
            _fail(f"tool {pin['id']} {pin['version']} has no packaged official artifact")
        _check_sha256(artifact.get("sha256"), f"official artifact {pin['id']}.sha256")

    runner_ref = registry["runner"]
    _expect_keys(runner_ref, {"id", "revision", "entry", "files"}, "runner")
    if (runner_ref["id"] != gate_runner.RUNNER_ID
            or runner_ref["revision"] != gate_runner.RUNNER_REVISION):
        _fail("packaged runner identity does not match the installed runner revision")
    files = runner_ref["files"]
    if not isinstance(files, list) or not files:
        _fail("runner files must be a non-empty array")
    entry_sha: str | None = None
    seen: set[str] = set()
    for item in files:
        _expect_keys(item, {"path", "sha256"}, "runner file")
        relative = item["path"]
        if (not isinstance(relative, str) or not relative or "\\" in relative
                or ":" in relative or relative.startswith("/")
                or ".." in Path(relative).parts):
            _fail(f"runner file path is not a safe relative path: {relative!r}")
        if relative in seen:
            _fail(f"duplicate runner file path: {relative}")
        seen.add(relative)
        expected = _check_sha256(item["sha256"], f"runner file {relative}.sha256")
        actual = _sha256(_read_bytes(base / relative, f"runner file {relative}"))
        if actual != expected:
            _fail(f"runner file {relative} SHA-256 mismatch: {actual} != {expected}")
        if relative == runner_ref["entry"]:
            entry_sha = expected
    if entry_sha is None:
        _fail("runner entry file is not bound by the packaged registry")

    return PackagedRegistry(
        runner={"id": runner_ref["id"], "revision": runner_ref["revision"],
                "sha256": entry_sha},
        gates=(gate,),
        tool_manifests=(tool_manifest,),
    )
