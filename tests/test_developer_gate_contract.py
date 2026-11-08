"""Focused unit tests for developer gate contract validation and safe tar foundations.

Covers:
- Strict UTF-8 JSON parsing (duplicate keys, non-finite numbers, bounds).
- Canonical JSON and SHA-256 computation.
- Pure-stdlib schema validator on closed schema subset.
- Semantic request validation against capabilities and owner policy pins.
- Bounded source tar validation and safe extraction into empty owned directories.
- Rejection of symlinks, hardlinks, sparse files, traversal, absolute paths, and aliasing.
- Bounded result archive validation (exact manifest.json and declared logs only).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.developer_gate_contract import (
    CONTRACT_ID,
    CONTRACT_REVISION,
    BoundedTarFile,
    BoundedTarInfo,
    GateContractError,
    canonical_json,
    compute_canonical_sha256,
    extract_source_archive,
    load_gate_schema,
    open_bounded_tar,
    parse_strict_json,
    sha256_file,
    sha256_stream,
    validate_capabilities_schema,
    validate_gate_request_schema,
    validate_policy_schema,
    validate_request_semantics,
    validate_result_archive,
    validate_result_manifest_schema,
    validate_schema,
    validate_source_archive,
)


def _make_runner() -> dict:
    return {
        "id": "studio.app-gate-runner.v1",
        "revision": "runner-rev-1",
        "sha256": "a" * 64,
    }


def _make_source(*, size: int = 100, expanded: int = 200, files: int = 2, sha: str = "b" * 64) -> dict:
    return {
        "sha256": sha,
        "size_bytes": size,
        "expanded_bytes": expanded,
        "file_count": files,
        "commit": "c" * 40,
        "media_type": "application/x-tar",
    }


def _make_tool_pin(tool_id: str = "python", version: str = "3.11.0") -> dict:
    return {
        "id": tool_id,
        "version": version,
        "manifest_sha256": "d" * 64,
    }


def _make_tool_manifest(pins: list[dict] | None = None) -> dict:
    tools = pins or [_make_tool_pin()]
    return {
        "format": "studio.tool-manifest.v1",
        "id": "tool-manifest-pilot",
        "revision": "tm-rev-1",
        "sha256": "e" * 64,
        "tools": tools,
        "provenance": "hub-packaged-official-hash-pinned",
    }


def _make_limits() -> dict:
    return {
        "runtime_seconds": 60,
        "workspace_bytes": 1_000_000,
        "result_spool_bytes": 500_000,
        "shared_cache_bytes": 100_000,
        "minimum_free_bytes": 100_000,
        "toolchain_install_bytes": 50_000,
        "max_process_tree_rss_bytes": 2_000_000,
        "max_system_swap_growth_bytes": 0,
        "start_min_free_memory_bytes": 500_000,
    }


def _make_gate(source: dict | None = None, tool_manifest: dict | None = None) -> dict:
    src = source or _make_source()
    tm = tool_manifest or _make_tool_manifest()
    return {
        "id": "gate-pilot-1",
        "revision": "gate-rev-1",
        "sha256": "f" * 64,
        "required_toolchains": tm["tools"],
        "source": src,
        "command_fixture_sha256": "1" * 64,
        "dependency_manifest_sha256": "2" * 64,
        "tool_manifest": tm,
        "limits": _make_limits(),
        "budget_provenance": "requested",
        "check_ids": ["check-compile", "check-test"],
    }


def _make_policy(*, controller_id: str = "ctrl-1", node_id: str = "node-1") -> dict:
    return {
        "format": "studio.developer-gate.policy.v1",
        "revision": "pol-rev-1",
        "minimum_internal_physical_bytes": 512_000_000_000,
        "allowed_targets": [{"controller_id": controller_id, "node_id": node_id}],
        "excluded_targets": [],
    }


def _make_bounds() -> dict:
    return {
        "json_bytes": 1_000_000,
        "source_bytes": 5_000_000,
        "source_expanded_bytes": 10_000_000,
        "source_files": 1000,
        "result_bytes": 5_000_000,
        "log_bytes": 1_000_000,
        "workspace_bytes": 2_000_000,
        "result_spool_bytes": 1_000_000,
        "shared_cache_bytes": 500_000,
        "runtime_seconds": 120,
        "tool_prepare_seconds": 60,
        "toolchain_install_bytes": 100_000,
        "max_process_tree_rss_bytes": 4_000_000,
        "max_system_swap_growth_bytes": 0,
        "inventory_max_age_seconds": 300,
        "result_files": 100,
        "workspace_enforcement": "monitored_stop",
        "sample_interval_seconds": 0.5,
    }


def _make_capabilities(gate: dict | None = None, policy: dict | None = None) -> dict:
    g = gate or _make_gate()
    pol = policy or _make_policy()
    tm = g["tool_manifest"]
    return {
        "format": "studio.developer-gate.capabilities.v1",
        "contract": CONTRACT_ID,
        "node_id": "node-1",
        "controller_id": "ctrl-1",
        "server_time": "2026-11-08T00:00:00Z",
        "supported": True,
        "reason": None,
        "runner": _make_runner(),
        "concurrency": 1,
        "setting": {
            "enabled": True,
            "verified": True,
            "revision": "set-rev-1",
        },
        "bounds": _make_bounds(),
        "stop_proof": {
            "revision": "sp-rev-1",
            "proof_sha256": "7" * 64,
            "stop_latency_seconds": 0.2,
        },
        "tool_manifests": [tm],
        "gates": [g],
        "inventory": {
            "format": "studio.developer-gate.inventory.v1",
            "node_id": "node-1",
            "controller_id": "ctrl-1",
            "observed_at": "2026-11-08T00:00:00Z",
            "internal_physical_drive": {
                "device_id": "disk0",
                "internal": True,
                "physical": True,
                "size_bytes": 512_000_000_000,
                "source": "diskutil-info-plist",
                "workspace_backing_device_id": "disk0",
                "backing_source": "diskutil-info-plist",
                "observed_at": "2026-11-08T00:00:00Z",
                "reason": None,
            },
            "free_bytes": 100_000_000_000,
            "toolchains": [
                {
                    "id": "python",
                    "pin": _make_tool_pin(),
                    "available": True,
                    "observed_at": "2026-11-08T00:00:00Z",
                    "installed_bytes": 10_000_000,
                    "provenance": "owned-toolchain-readback",
                    "reason": None,
                }
            ],
            "developer_gate_space": {
                "observed_at": "2026-11-08T00:00:00Z",
                "workspace_bytes": 0,
                "result_spool_bytes": 0,
                "shared_cache_bytes": 0,
                "workspace_limit_bytes": 1_000_000_000,
                "result_spool_limit_bytes": 500_000_000,
                "shared_cache_limit_bytes": 200_000_000,
                "cleanup_state": "none",
                "reason": None,
            },
        },
        "admission_policy": pol,
    }


def _make_gate_request(gate: dict | None = None, policy: dict | None = None) -> dict:
    g = gate or _make_gate()
    pol = policy or _make_policy()
    return {
        "format": "studio.developer-gate.request.v1",
        "run_id": "run-20261108-01",
        "test_id": "test-pilot-01",
        "runner": _make_runner(),
        "node_setting_revision": "set-rev-1",
        "admission_policy_revision": pol["revision"],
        "minimum_internal_physical_bytes": pol["minimum_internal_physical_bytes"],
        "requested_limits": _make_limits(),
        "gate": g,
        "source": g["source"],
        "tool_manifest": g["tool_manifest"],
    }


def _make_result_manifest(request: dict, *, outcome: str = "passed", exit_code: int = 0) -> tuple[dict, dict[str, bytes]]:
    logs: dict[str, bytes] = {}
    checks = []
    for cid in request["gate"]["check_ids"]:
        log_bytes = f"log output for {cid}\n".encode("utf-8")
        logs[f"logs/{cid}.log"] = log_bytes
        checks.append({
            "id": cid,
            "outcome": outcome,
            "exit_code": exit_code,
            "duration_seconds": 1.2,
            "log_sha256": hashlib.sha256(log_bytes).hexdigest(),
            "log_bytes": len(log_bytes),
            "reason": None if outcome == "passed" else {"code": "failed", "detail": "check failed"},
        })

    manifest = {
        "format": "studio.developer-gate.result.v1",
        "acceptance": {
            "attempt_id": "att-100",
            "idempotency_key": "idemp-key-100",
            "request_sha256": compute_canonical_sha256(request),
            "node_id": "node-1",
            "controller_id": "ctrl-1",
        },
        "runner": request["runner"],
        "gate": request["gate"],
        "source": request["source"],
        "tool_manifest": request["tool_manifest"],
        "actual_toolchains": [
            {
                "id": "python",
                "pin": _make_tool_pin(),
                "available": True,
                "observed_at": "2026-11-08T00:00:00Z",
                "installed_bytes": 10_000_000,
                "provenance": "owned-toolchain-readback",
                "reason": None,
            }
        ],
        "outcome": outcome,
        "exit_code": exit_code,
        "termination_signal": None,
        "reason": None if outcome == "passed" else {"code": "test_failed", "detail": "Gate failed"},
        "checks": checks,
        "started_at": "2026-11-08T00:00:00Z",
        "finished_at": "2026-11-08T00:00:03Z",
        "process_release": {
            "confirmed": True,
            "observed_at": "2026-11-08T00:00:03Z",
            "owned_processes_remaining": 0,
            "proof": "owned-process-identities-exited",
        },
        "internal_physical_drive": {
            "device_id": "disk0",
            "internal": True,
            "physical": True,
            "size_bytes": 512_000_000_000,
            "source": "diskutil-info-plist",
            "workspace_backing_device_id": "disk0",
            "backing_source": "diskutil-info-plist",
            "observed_at": "2026-11-08T00:00:00Z",
            "reason": None,
        },
        "before": {
            "observed_at": "2026-11-08T00:00:00Z",
            "free_bytes": 100_000_000_000,
            "workspace_bytes": 0,
            "result_spool_bytes": 0,
            "shared_cache_bytes": 0,
        },
        "peak_workspace_bytes": 2048,
    }
    return manifest, logs


class DeveloperGateContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # -----------------------------------------------------------------------
    # Strict JSON and Canonical Hashing Tests
    # -----------------------------------------------------------------------

    def test_parse_strict_json_rejection_of_duplicates(self) -> None:
        raw = b'{"key": 1, "key": 2}'
        with self.assertRaises(GateContractError) as ctx:
            parse_strict_json(raw)
        self.assertIn("Duplicate key", str(ctx.exception))

    def test_parse_strict_json_rejection_of_nonfinite(self) -> None:
        for invalid in (b'{"val": NaN}', b'{"val": Infinity}', b'{"val": -Infinity}'):
            with self.subTest(invalid=invalid):
                with self.assertRaises(GateContractError) as ctx:
                    parse_strict_json(invalid)
                self.assertIn("Non-finite", str(ctx.exception))

    def test_parse_strict_json_rejection_of_invalid_utf8(self) -> None:
        invalid = b'{"msg": "\xff\xfe"}'
        with self.assertRaises(GateContractError) as ctx:
            parse_strict_json(invalid)
        self.assertIn("Invalid UTF-8", str(ctx.exception))

    def test_parse_strict_json_bounds_ceiling(self) -> None:
        payload = b'{"a": 123}'
        with self.assertRaises(GateContractError) as ctx:
            parse_strict_json(payload, max_bytes=5)
        self.assertEqual(ctx.exception.status, 413)

    def test_canonical_json_and_sha256(self) -> None:
        data = {"b": 2, "a": 1, "z": [3, 2, 1]}
        canon = canonical_json(data)
        self.assertEqual(canon, '{"a":1,"b":2,"z":[3,2,1]}')
        expected_sha = hashlib.sha256(canon.encode("ascii")).hexdigest()
        self.assertEqual(compute_canonical_sha256(data), expected_sha)

    # -----------------------------------------------------------------------
    # Schema Validator Tests
    # -----------------------------------------------------------------------

    def test_schema_valid_gate_request(self) -> None:
        req = _make_gate_request()
        validate_gate_request_schema(req)

    def test_schema_rejection_of_additional_properties(self) -> None:
        req = _make_gate_request()
        req["unauthorized_injected_field"] = "payload"
        with self.assertRaises(GateContractError) as ctx:
            validate_gate_request_schema(req)
        self.assertIn("Unrecognized property", str(ctx.exception))

    def test_schema_rejection_of_missing_required_property(self) -> None:
        req = _make_gate_request()
        del req["runner"]
        with self.assertRaises(GateContractError) as ctx:
            validate_gate_request_schema(req)
        self.assertIn("Missing required property", str(ctx.exception))

    def test_schema_rejection_of_invalid_pattern(self) -> None:
        req = _make_gate_request()
        req["runner"]["sha256"] = "not-a-64-char-hex"
        with self.assertRaises(GateContractError) as ctx:
            validate_gate_request_schema(req)
        self.assertIn("pattern", str(ctx.exception))

    # -----------------------------------------------------------------------
    # Semantic Request Validation Tests
    # -----------------------------------------------------------------------

    def test_semantic_validation_matching_request(self) -> None:
        gate = _make_gate()
        policy = _make_policy()
        caps = _make_capabilities(gate=gate, policy=policy)
        req = _make_gate_request(gate=gate, policy=policy)
        validate_request_semantics(req, caps)

    def test_semantic_validation_unsupported_node(self) -> None:
        caps = _make_capabilities()
        caps["supported"] = False
        caps["reason"] = {"code": "offline", "detail": "Node in maintenance"}
        req = _make_gate_request()
        with self.assertRaises(GateContractError) as ctx:
            validate_request_semantics(req, caps)
        self.assertEqual(ctx.exception.status, 503)

    def test_semantic_validation_disabled_setting(self) -> None:
        caps = _make_capabilities()
        caps["setting"]["enabled"] = False
        req = _make_gate_request()
        with self.assertRaises(GateContractError) as ctx:
            validate_request_semantics(req, caps)
        self.assertEqual(ctx.exception.status, 409)

    def test_semantic_validation_unverified_setting(self) -> None:
        caps = _make_capabilities()
        caps["setting"]["verified"] = False
        req = _make_gate_request()
        with self.assertRaises(GateContractError) as ctx:
            validate_request_semantics(req, caps)
        self.assertEqual(ctx.exception.status, 409)
        self.assertIn("unverified", str(ctx.exception))

    def test_semantic_validation_empty_allowed_targets_denies(self) -> None:
        policy = _make_policy()
        policy["allowed_targets"] = []
        caps = _make_capabilities(policy=policy)
        req = _make_gate_request(policy=policy)
        with self.assertRaises(GateContractError) as ctx:
            validate_request_semantics(req, caps)
        self.assertEqual(ctx.exception.status, 409)
        self.assertIn("not in admission policy allowed_targets", str(ctx.exception))

    def test_semantic_validation_target_mismatch(self) -> None:
        caps = _make_capabilities()
        req = _make_gate_request()
        with self.assertRaises(GateContractError) as ctx:
            validate_request_semantics(req, caps, target_node_id="wrong-node")
        self.assertEqual(ctx.exception.status, 409)
        self.assertIn("Target node_id mismatch", str(ctx.exception))

    def test_semantic_validation_duplicate_advertised_pins(self) -> None:
        caps = _make_capabilities()
        g = _make_gate()
        caps["gates"] = [g, g]
        req = _make_gate_request()
        with self.assertRaises(GateContractError) as ctx:
            validate_request_semantics(req, caps)
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("Duplicate gate identifiers", str(ctx.exception))

    def test_semantic_validation_safety_floors_cannot_be_lowered(self) -> None:
        caps = _make_capabilities()
        req = _make_gate_request()
        req["requested_limits"]["minimum_free_bytes"] = 1  # lower than gate limit (100_000)
        with self.assertRaises(GateContractError) as ctx:
            validate_request_semantics(req, caps)
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("minimum_free_bytes", str(ctx.exception))

    def test_semantic_validation_stale_setting_revision(self) -> None:
        caps = _make_capabilities()
        caps["setting"]["revision"] = "rev-newer"
        req = _make_gate_request()
        with self.assertRaises(GateContractError) as ctx:
            validate_request_semantics(req, caps)
        self.assertEqual(ctx.exception.status, 409)

    def test_semantic_validation_policy_revision_mismatch(self) -> None:
        caps = _make_capabilities()
        caps["admission_policy"]["revision"] = "pol-newer"
        req = _make_gate_request()
        with self.assertRaises(GateContractError) as ctx:
            validate_request_semantics(req, caps)
        self.assertEqual(ctx.exception.status, 409)

    def test_semantic_validation_policy_excluded_target(self) -> None:
        policy = _make_policy()
        policy["excluded_targets"] = [{"controller_id": "ctrl-1", "node_id": "node-1"}]
        caps = _make_capabilities(policy=policy)
        req = _make_gate_request(policy=policy)
        with self.assertRaises(GateContractError) as ctx:
            validate_request_semantics(req, caps, target_controller_id="ctrl-1", target_node_id="node-1")
        self.assertEqual(ctx.exception.status, 409)

    def test_semantic_validation_runner_mismatch(self) -> None:
        caps = _make_capabilities()
        req = _make_gate_request()
        req["runner"]["revision"] = "different-revision"
        with self.assertRaises(GateContractError) as ctx:
            validate_request_semantics(req, caps)
        self.assertEqual(ctx.exception.status, 400)

    def test_semantic_validation_unadvertised_gate(self) -> None:
        caps = _make_capabilities()
        req = _make_gate_request()
        req["gate"]["id"] = "gate-unadvertised"
        with self.assertRaises(GateContractError) as ctx:
            validate_request_semantics(req, caps)
        self.assertEqual(ctx.exception.status, 400)

    def test_semantic_validation_limit_exceeds_bounds(self) -> None:
        caps = _make_capabilities()
        req = _make_gate_request()
        # requested limit exceeds gate manifest and node bounds
        req["requested_limits"]["runtime_seconds"] = 999999
        with self.assertRaises(GateContractError) as ctx:
            validate_request_semantics(req, caps)
        self.assertEqual(ctx.exception.status, 413)

    def test_semantic_validation_caller_url_in_run_id_rejected(self) -> None:
        caps = _make_capabilities()
        req = _make_gate_request()
        req["run_id"] = "http://evil.com/run"
        with self.assertRaises(GateContractError) as ctx:
            validate_request_semantics(req, caps)
        self.assertIn("URL scheme not permitted", str(ctx.exception))

    # -----------------------------------------------------------------------
    # Source Tar Validation and Safe Extraction Tests
    # -----------------------------------------------------------------------

    def _create_tar_archive(self, members: list[tuple[str, bytes | None, int]]) -> tuple[bytes, int, int]:
        bio = io.BytesIO()
        total_files = 0
        total_expanded = 0
        with tarfile.open(fileobj=bio, mode="w:") as tar:
            for name, content, member_type in members:
                ti = tarfile.TarInfo(name=name)
                ti.type = member_type
                if content is not None:
                    ti.size = len(content)
                    total_files += 1
                    total_expanded += len(content)
                    tar.addfile(ti, io.BytesIO(content))
                else:
                    tar.addfile(ti)
        data = bio.getvalue()
        return data, total_files, total_expanded

    def test_source_archive_valid_extraction(self) -> None:
        f1_content = b"print('hello world')\n"
        f2_content = b"name = 'hub pilot'\n"
        data, files, expanded = self._create_tar_archive([
            ("src", None, tarfile.DIRTYPE),
            ("src/main.py", f1_content, tarfile.REGTYPE),
            ("src/config.py", f2_content, tarfile.REGTYPE),
        ])
        sha = hashlib.sha256(data).hexdigest()
        source_decl = _make_source(size=len(data), expanded=expanded, files=files, sha=sha)

        dest_dir = self.tmp_path / "extracted_src"
        result = extract_source_archive(io.BytesIO(data), dest_dir, source_decl)
        self.assertEqual(result["file_count"], 2)
        self.assertEqual(result["expanded_bytes"], expanded)
        self.assertTrue((dest_dir / "src" / "main.py").exists())
        self.assertEqual((dest_dir / "src" / "main.py").read_bytes(), f1_content)
        self.assertEqual((dest_dir / "src" / "config.py").read_bytes(), f2_content)

    def test_source_archive_raw_sha_mismatch_rejected(self) -> None:
        data, files, expanded = self._create_tar_archive([
            ("file.txt", b"content\n", tarfile.REGTYPE)
        ])
        source_decl = _make_source(size=len(data), expanded=expanded, files=files, sha="0" * 64)
        with self.assertRaises(GateContractError) as ctx:
            validate_source_archive(io.BytesIO(data), source_decl)
        self.assertIn("SHA-256 mismatch", str(ctx.exception))

    def test_source_archive_rejects_symlink(self) -> None:
        bio = io.BytesIO()
        with tarfile.open(fileobj=bio, mode="w:") as tar:
            ti = tarfile.TarInfo(name="link.txt")
            ti.type = tarfile.SYMTYPE
            ti.linkname = "/etc/passwd"
            tar.addfile(ti)
        data = bio.getvalue()
        source_decl = _make_source(size=len(data), expanded=10, files=1, sha=hashlib.sha256(data).hexdigest())
        with self.assertRaises(GateContractError) as ctx:
            validate_source_archive(io.BytesIO(data), source_decl)
        self.assertIn("Symlinks are forbidden", str(ctx.exception))

    def test_source_archive_rejects_hardlink(self) -> None:
        bio = io.BytesIO()
        with tarfile.open(fileobj=bio, mode="w:") as tar:
            ti = tarfile.TarInfo(name="hardlink.txt")
            ti.type = tarfile.LNKTYPE
            ti.linkname = "original.txt"
            tar.addfile(ti)
        data = bio.getvalue()
        source_decl = _make_source(size=len(data), expanded=10, files=1, sha=hashlib.sha256(data).hexdigest())
        with self.assertRaises(GateContractError) as ctx:
            validate_source_archive(io.BytesIO(data), source_decl)
        self.assertIn("Hardlinks are forbidden", str(ctx.exception))

    def test_source_archive_rejects_sparse(self) -> None:
        bio = io.BytesIO()
        with tarfile.open(fileobj=bio, mode="w:") as tar:
            ti = tarfile.TarInfo(name="sparse.bin")
            ti.type = b"S"  # GNUTYPE_SPARSE
            ti.size = 100
            tar.addfile(ti, io.BytesIO(b"\0" * 100))
        data = bio.getvalue()
        source_decl = _make_source(size=len(data), expanded=100, files=1, sha=hashlib.sha256(data).hexdigest())
        with self.assertRaises(GateContractError) as ctx:
            validate_source_archive(io.BytesIO(data), source_decl)
        self.assertIn("Sparse files are forbidden", str(ctx.exception))

    def test_source_archive_rejects_path_traversal(self) -> None:
        data, files, expanded = self._create_tar_archive([
            ("../escape.txt", b"bad\n", tarfile.REGTYPE)
        ])
        source_decl = _make_source(size=len(data), expanded=expanded, files=files, sha=hashlib.sha256(data).hexdigest())
        with self.assertRaises(GateContractError) as ctx:
            validate_source_archive(io.BytesIO(data), source_decl)
        self.assertIn("Path traversal forbidden", str(ctx.exception))
        self.assertNotIn("escape.txt", str(ctx.exception))

    def test_source_archive_rejects_absolute_path(self) -> None:
        data, files, expanded = self._create_tar_archive([
            ("/etc/shadow", b"bad\n", tarfile.REGTYPE)
        ])
        source_decl = _make_source(size=len(data), expanded=expanded, files=files, sha=hashlib.sha256(data).hexdigest())
        with self.assertRaises(GateContractError) as ctx:
            validate_source_archive(io.BytesIO(data), source_decl)
        self.assertIn("Absolute member path forbidden", str(ctx.exception))
        self.assertNotIn("/etc/shadow", str(ctx.exception))

    def test_source_archive_rejects_duplicate_or_aliased_path(self) -> None:
        # Same path twice
        data, files, expanded = self._create_tar_archive([
            ("file.txt", b"first\n", tarfile.REGTYPE),
            ("file.txt", b"second\n", tarfile.REGTYPE),
        ])
        source_decl = _make_source(size=len(data), expanded=expanded, files=files, sha=hashlib.sha256(data).hexdigest())
        with self.assertRaises(GateContractError) as ctx:
            validate_source_archive(io.BytesIO(data), source_decl)
        self.assertIn("Duplicate or aliased", str(ctx.exception))

    def test_source_archive_rejects_case_aliased_path(self) -> None:
        # Case collision
        data, files, expanded = self._create_tar_archive([
            ("file.txt", b"first\n", tarfile.REGTYPE),
            ("FILE.TXT", b"second\n", tarfile.REGTYPE),
        ])
        source_decl = _make_source(size=len(data), expanded=expanded, files=files, sha=hashlib.sha256(data).hexdigest())
        with self.assertRaises(GateContractError) as ctx:
            validate_source_archive(io.BytesIO(data), source_decl)
        self.assertIn("Case-collision", str(ctx.exception))

    def test_source_archive_extraction_refuses_nonempty_destination(self) -> None:
        dest_dir = self.tmp_path / "nonempty"
        dest_dir.mkdir()
        (dest_dir / "existing.txt").write_text("already here")

        data, files, expanded = self._create_tar_archive([
            ("src.py", b"pass\n", tarfile.REGTYPE)
        ])
        source_decl = _make_source(size=len(data), expanded=expanded, files=files, sha=hashlib.sha256(data).hexdigest())
        with self.assertRaises(GateContractError) as ctx:
            extract_source_archive(io.BytesIO(data), dest_dir, source_decl)
        self.assertIn("must be empty", str(ctx.exception))

    def test_source_archive_extraction_refuses_symlink_destination(self) -> None:
        real_dir = self.tmp_path / "real_dir"
        real_dir.mkdir()
        sym_dir = self.tmp_path / "sym_dir"
        sym_dir.symlink_to(real_dir)

        data, files, expanded = self._create_tar_archive([
            ("src.py", b"pass\n", tarfile.REGTYPE)
        ])
        source_decl = _make_source(size=len(data), expanded=expanded, files=files, sha=hashlib.sha256(data).hexdigest())
        with self.assertRaises(GateContractError) as ctx:
            extract_source_archive(io.BytesIO(data), sym_dir, source_decl)
        self.assertIn("must not be a symlink", str(ctx.exception))

    # -----------------------------------------------------------------------
    # Result Tar Validation Tests
    # -----------------------------------------------------------------------

    def _create_result_tar(self, manifest: dict, logs: dict[str, bytes], extra_entries: list[tuple[str, bytes | None, int]] | None = None) -> bytes:
        bio = io.BytesIO()
        with tarfile.open(fileobj=bio, mode="w:") as tar:
            # manifest.json
            manifest_bytes = canonical_json(manifest).encode("utf-8")
            ti = tarfile.TarInfo(name="manifest.json")
            ti.size = len(manifest_bytes)
            ti.type = tarfile.REGTYPE
            tar.addfile(ti, io.BytesIO(manifest_bytes))

            # logs
            for log_name, log_bytes in logs.items():
                ti_log = tarfile.TarInfo(name=log_name)
                ti_log.size = len(log_bytes)
                ti_log.type = tarfile.REGTYPE
                tar.addfile(ti_log, io.BytesIO(log_bytes))

            if extra_entries:
                for name, content, m_type in extra_entries:
                    ti_ex = tarfile.TarInfo(name=name)
                    ti_ex.type = m_type
                    if content is not None:
                        ti_ex.size = len(content)
                        tar.addfile(ti_ex, io.BytesIO(content))
                    else:
                        tar.addfile(ti_ex)
        return bio.getvalue()

    def test_result_archive_valid(self) -> None:
        req = _make_gate_request()
        manifest, logs = _make_result_manifest(req, outcome="passed", exit_code=0)
        data = self._create_result_tar(manifest, logs)
        descriptor = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "media_type": "application/x-tar",
        }
        res = validate_result_archive(
            io.BytesIO(data),
            expected_acceptance=manifest["acceptance"],
            expected_descriptor=descriptor,
            expected_request=req,
        )
        self.assertEqual(res["outcome"], "passed")
        self.assertEqual(res["exit_code"], 0)

    def test_result_archive_rejects_directory_entries(self) -> None:
        req = _make_gate_request()
        manifest, logs = _make_result_manifest(req)
        # Add a directory entry for "logs"
        data = self._create_result_tar(manifest, logs, extra_entries=[("logs", None, tarfile.DIRTYPE)])
        with self.assertRaises(GateContractError) as ctx:
            validate_result_archive(io.BytesIO(data), expected_acceptance=manifest["acceptance"])
        self.assertIn("Directory entries forbidden", str(ctx.exception))

    def test_result_archive_rejects_unlisted_members(self) -> None:
        req = _make_gate_request()
        manifest, logs = _make_result_manifest(req)
        data = self._create_result_tar(manifest, logs, extra_entries=[("extra.txt", b"leak\n", tarfile.REGTYPE)])
        with self.assertRaises(GateContractError) as ctx:
            validate_result_archive(io.BytesIO(data), expected_acceptance=manifest["acceptance"])
        self.assertIn("do not match declared manifest", str(ctx.exception))

    def test_result_archive_rejects_log_sha_mismatch(self) -> None:
        req = _make_gate_request()
        manifest, logs = _make_result_manifest(req)
        manifest["checks"][0]["log_sha256"] = "0" * 64
        data = self._create_result_tar(manifest, logs)
        with self.assertRaises(GateContractError) as ctx:
            validate_result_archive(io.BytesIO(data), expected_acceptance=manifest["acceptance"])
        self.assertIn("log SHA-256 mismatch", str(ctx.exception))

    def test_result_archive_rejects_log_invalid_utf8(self) -> None:
        req = _make_gate_request()
        manifest, logs = _make_result_manifest(req)
        bad_bytes = b"invalid \xff\xfe log\n"
        cid = req["gate"]["check_ids"][0]
        logs[f"logs/{cid}.log"] = bad_bytes
        manifest["checks"][0]["log_bytes"] = len(bad_bytes)
        manifest["checks"][0]["log_sha256"] = hashlib.sha256(bad_bytes).hexdigest()
        data = self._create_result_tar(manifest, logs)
        with self.assertRaises(GateContractError) as ctx:
            validate_result_archive(io.BytesIO(data), expected_acceptance=manifest["acceptance"])
        self.assertIn("invalid UTF-8", str(ctx.exception))

    def test_result_archive_rejects_passed_with_nonzero_exit(self) -> None:
        req = _make_gate_request()
        manifest, logs = _make_result_manifest(req, outcome="passed", exit_code=1)
        data = self._create_result_tar(manifest, logs)
        with self.assertRaises(GateContractError) as ctx:
            validate_result_archive(io.BytesIO(data), expected_acceptance=manifest["acceptance"])
        self.assertIn("Passed outcome must have exit_code 0", str(ctx.exception))

    def test_result_archive_rejects_failed_with_zero_exit(self) -> None:
        req = _make_gate_request()
        manifest, logs = _make_result_manifest(req, outcome="failed", exit_code=0)
        data = self._create_result_tar(manifest, logs)
        with self.assertRaises(GateContractError) as ctx:
            validate_result_archive(io.BytesIO(data), expected_acceptance=manifest["acceptance"])
        self.assertIn("Non-passed outcome (failed) cannot have exit_code 0", str(ctx.exception))

    def test_result_archive_rejects_missing_manifest(self) -> None:
        req = _make_gate_request()
        manifest, logs = _make_result_manifest(req)
        bio = io.BytesIO()
        with tarfile.open(fileobj=bio, mode="w:") as tar:
            for log_name, log_bytes in logs.items():
                ti = tarfile.TarInfo(name=log_name)
                ti.size = len(log_bytes)
                tar.addfile(ti, io.BytesIO(log_bytes))
        data = bio.getvalue()
        with self.assertRaises(GateContractError) as ctx:
            validate_result_archive(io.BytesIO(data), expected_acceptance=manifest["acceptance"])
        self.assertIn("Missing manifest.json", str(ctx.exception))

    def test_result_archive_rejects_sparse_file(self) -> None:
        req = _make_gate_request()
        manifest, logs = _make_result_manifest(req)
        bio = io.BytesIO()
        with tarfile.open(fileobj=bio, mode="w:") as tar:
            # manifest.json
            m_bytes = canonical_json(manifest).encode("utf-8")
            ti = tarfile.TarInfo(name="manifest.json")
            ti.size = len(m_bytes)
            tar.addfile(ti, io.BytesIO(m_bytes))
            # sparse log
            ti_sp = tarfile.TarInfo(name="logs/check-compile.log")
            ti_sp.type = b"S"
            ti_sp.size = 10
            tar.addfile(ti_sp, io.BytesIO(b"\0" * 10))
        data = bio.getvalue()
        with self.assertRaises(GateContractError) as ctx:
            validate_result_archive(io.BytesIO(data), expected_acceptance=manifest["acceptance"])
        self.assertIn("Sparse files are forbidden", str(ctx.exception))

    def test_result_archive_rejects_acceptance_mismatch(self) -> None:
        req = _make_gate_request()
        manifest, logs = _make_result_manifest(req)
        data = self._create_result_tar(manifest, logs)
        wrong_acceptance = dict(manifest["acceptance"])
        wrong_acceptance["attempt_id"] = "wrong-attempt-id"
        with self.assertRaises(GateContractError) as ctx:
            validate_result_archive(io.BytesIO(data), expected_acceptance=wrong_acceptance)
        self.assertIn("does not match expected acceptance", str(ctx.exception))

    def test_result_archive_rejects_missing_actual_tools_for_passed(self) -> None:
        req = _make_gate_request()
        manifest, logs = _make_result_manifest(req)
        manifest["actual_toolchains"] = []
        data = self._create_result_tar(manifest, logs)
        with self.assertRaises(GateContractError) as ctx:
            validate_result_archive(io.BytesIO(data), expected_acceptance=manifest["acceptance"])
        self.assertIn("actual_toolchains", str(ctx.exception))

    def test_result_archive_rejects_aggregate_log_cap_exceeded(self) -> None:
        req = _make_gate_request()
        manifest, logs = _make_result_manifest(req)
        # Create big logs
        cid = req["gate"]["check_ids"][0]
        big_log = b"a" * 2000
        logs[f"logs/{cid}.log"] = big_log
        manifest["checks"][0]["log_bytes"] = len(big_log)
        manifest["checks"][0]["log_sha256"] = hashlib.sha256(big_log).hexdigest()
        data = self._create_result_tar(manifest, logs)
        with self.assertRaises(GateContractError) as ctx:
            validate_result_archive(
                io.BytesIO(data),
                expected_acceptance=manifest["acceptance"],
                bounds={"result_bytes": 100000, "log_bytes": 1000, "result_files": 10},
            )
        self.assertIn("exceeds bound", str(ctx.exception))

    def test_result_archive_rejects_unconfirmed_process_release(self) -> None:
        req = _make_gate_request()
        manifest, logs = _make_result_manifest(req, outcome="failed", exit_code=1)
        manifest["process_release"]["confirmed"] = False
        data = self._create_result_tar(manifest, logs)
        with self.assertRaises(GateContractError) as ctx:
            validate_result_archive(io.BytesIO(data), expected_acceptance=manifest["acceptance"])
        self.assertTrue(
            "Process release confirmed is required" in str(ctx.exception)
            or "Expected True at $.process_release.confirmed" in str(ctx.exception)
        )

    def test_result_archive_rejects_canonical_request_sha_mismatch(self) -> None:
        req = _make_gate_request()
        manifest, logs = _make_result_manifest(req)
        manifest["acceptance"]["request_sha256"] = "0" * 64
        data = self._create_result_tar(manifest, logs)
        with self.assertRaises(GateContractError) as ctx:
            validate_result_archive(
                io.BytesIO(data),
                expected_acceptance=manifest["acceptance"],
                expected_request=req,
            )
        self.assertIn("mismatch with canonical request", str(ctx.exception))

    def test_cancel_ack_error_schemas(self) -> None:
        cancel = {
            "format": "studio.developer-gate.cancel.v1",
            "request_id": "req-cancel-01",
        }
        validate_schema(cancel, "cancelRequest")

        ack = {
            "format": "studio.developer-gate.ack.v1",
            "request_id": "req-ack-01",
            "acceptance": {
                "attempt_id": "att-1",
                "idempotency_key": "idemp-1",
                "request_sha256": "1" * 64,
                "node_id": "node-1",
                "controller_id": "ctrl-1",
            },
            "result": {
                "sha256": "2" * 64,
                "size_bytes": 1000,
                "media_type": "application/x-tar",
            },
        }
        validate_schema(ack, "ackRequest")

        err = GateContractError("Malformed request payload", status=400, code="malformed")
        err_dict = err.to_error_dict(acceptance=ack["acceptance"])
        validate_schema(err_dict, "error")

    def test_known_wire_pilot_source_values_conform_to_schema(self) -> None:
        known_src = {
            "commit": "d9c950547cb119c6aa57c54f9e2014a678593692",
            "size_bytes": 1689600,
            "expanded_bytes": 5000000,
            "file_count": 150,
            "sha256": "103a4d5d882726c035f12e5d0b21587d6d8f2da74c51f4583c25e5c41cf6a8b2",
            "media_type": "application/x-tar",
        }
        validate_schema(known_src, "source")

    def test_bounded_tar_rejects_huge_declared_pax_metadata_without_large_read(self) -> None:
        """Spy stream proves BoundedTarInfo rejects huge declared PAX header without reading 1 GB."""
        # Build 512-byte tar header with PAX type 'x' and huge declared size (1 GB = 1073741824 bytes = 0o10000000000)
        # Standard ustar header: name (100b), mode (8b), uid (8b), gid (8b), size (12b), mtime (12b), chksum (8b), typeflag (1b)
        header = bytearray(512)
        header[0:16] = b"pax_huge_header\0"
        header[100:108] = b"0000644\0"
        header[108:116] = b"0000000\0"
        header[116:124] = b"0000000\0"
        # 1 GB in octal: 10000000000 (11 chars + space or null)
        header[124:136] = b"10000000000\0"
        header[136:148] = b"00000000000\0"
        header[148:156] = b"        "  # checksum placeholder
        header[156] = ord("x")  # XHDTYPE
        header[257:263] = b"ustar\0"
        header[263:265] = b"00"
        # Compute checksum
        chksum = sum(header)
        header[148:156] = f"{chksum:06o}\0 ".encode("ascii")

        raw_header_bytes = bytes(header)

        class SpyStream(io.RawIOBase):
            def __init__(self, header_data: bytes) -> None:
                self._buf = header_data
                self._pos = 0
                self.bytes_read = 0

            def readable(self) -> bool:
                return True

            def readinto(self, b: bytearray | memoryview) -> int:
                if self._pos >= len(self._buf):
                    # If reader tries to read the 1GB payload, fail or track
                    # We only provide zeros if requested, but track read volume
                    n = min(len(b), 65536)
                    self.bytes_read += n
                    return n
                avail = len(self._buf) - self._pos
                to_copy = min(len(b), avail)
                b[:to_copy] = self._buf[self._pos : self._pos + to_copy]
                self._pos += to_copy
                self.bytes_read += to_copy
                return to_copy

            def seekable(self) -> bool:
                return True

            def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
                if whence == os.SEEK_SET:
                    self._pos = offset
                elif whence == os.SEEK_CUR:
                    self._pos += offset
                return self._pos

            def tell(self) -> int:
                return self._pos

        spy = io.BufferedReader(SpyStream(raw_header_bytes))  # type: ignore[arg-type]
        with self.assertRaises(GateContractError) as ctx:
            with open_bounded_tar(fileobj=spy, mode="r:") as tar:
                tar.next()
        self.assertIn("PAX metadata header exceeds size ceiling", str(ctx.exception))
        # Proves no large read occurred: only header block was read, not 1 GB
        self.assertLess(spy.raw.bytes_read, 128 * 1024)

    def test_bounded_tar_accepts_git_archive_global_pax_comment(self) -> None:
        """Ordinary git archive global PAX comment (commit SHA) is accepted by bounded tar."""
        bio = io.BytesIO()
        ti_pax = tarfile.TarInfo(name="pax_global_header")
        ti_pax.type = tarfile.XGLTYPE
        comment_bytes = b"52 comment=d9c950547cb119c6aa57c54f9e2014a678593692\n"
        ti_pax.size = len(comment_bytes)

        ti_file = tarfile.TarInfo(name="VERSION")
        content = b"0.6.205\n"
        ti_file.size = len(content)

        with tarfile.open(fileobj=bio, mode="w:") as tar:
            tar.addfile(ti_pax, io.BytesIO(comment_bytes))
            tar.addfile(ti_file, io.BytesIO(content))

        data = bio.getvalue()
        sha = hashlib.sha256(data).hexdigest()
        source_decl = _make_source(size=len(data), expanded=len(content), files=1, sha=sha)

        dest_dir = self.tmp_path / "git_archive_extracted"
        result = extract_source_archive(io.BytesIO(data), dest_dir, source_decl)
        self.assertEqual(result["file_count"], 1)
        self.assertEqual((dest_dir / "VERSION").read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
