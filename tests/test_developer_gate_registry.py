"""Tests for the packaged Library portable developer-gate registry loader.

Covers exact raw-byte identity binding, frozen schema validation, tamper rejection,
missing packaged inputs, self-referential hash refusal, zero placeholder refusal and a
staged relocation of the same layout. No Hub start, network, build or real library.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub import developer_gate_contract as contract
from components.hub import developer_gate_authority as authority_mod
from components.hub import developer_gate_runner as gate_runner
from components.hub.developer_gate_registry import RegistryError, load_packaged_registry

PACKAGING = ROOT / "packaging" / "developer-gates"
RUNNER_FILES = (
    "components/hub/developer_gate_runner.py",
    "components/hub/developer_image_runner.py",
)
PACKAGED_FILES = (
    "packaging/developer-gates/library-portable.gate.json",
    "packaging/developer-gates/library-portable.commands.json",
    "packaging/developer-gates/library-portable.dependency-manifest.json",
    "packaging/developer-gates/official-tools.json",
    "packaging/developer-gates/library-portable.registry.json",
    *RUNNER_FILES,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


NODE = "node-1"
CONTROLLER = "ctrl-1"


def _policy(allowed=None, excluded=()) -> dict:
    return {
        "format": "studio.developer-gate.policy.v1", "revision": "operator-rev-1",
        "minimum_internal_physical_bytes": 500_000_000_000,
        "allowed_targets": ([{"controller_id": CONTROLLER, "node_id": NODE}]
                            if allowed is None else allowed),
        "excluded_targets": list(excluded),
    }


def _proof(root: Path) -> dict:
    release = {"confirmed": True, "owned_processes_remaining": 0,
               "signal": "SIGTERM", "observed_after_seconds": 0.02}
    identities = [{"pid": 4321, "create_time": 1699999999.0, "alive_after": False}]
    return {
        "format": authority_mod.PROOF_FORMAT, "scope": authority_mod.PROOF_SCOPE,
        "created_at": 1762560000.0,
        # Extra actual metadata is allowed; this Library fixture commit must not be read as
        # a stale Hub source.
        "source_commit": "d9c950547cb119c6aa57c54f9e2014a678593692",
        "host": {"os": sys.platform, "arch": platform.machine()},
        "sources": {key: {"sha256": _sha(root / relative)}
                    for key, relative in authority_mod._SOURCE_FILES.items()},
        "scenarios": [
            {"name": "first-check-exit", "outcome": "failed", "exit_code": 7,
             "process_release": dict(release), "checked_identities": [dict(identities[0])],
             "group_gone": True, "ok": True},
            {"name": "owned-preemption", "outcome": "interrupted", "exit_code": None,
             "process_release": dict(release), "checked_identities": [dict(identities[0])],
             "group_gone": True, "ok": True, "stop_latency_seconds": 0.073},
        ],
        "limitations": ["local host runner entry only; no fleet or install claim"],
    }


def _receipt(root: Path, proof: dict | None = None, *, rehash: bool = True) -> dict:
    proof = proof if proof is not None else _proof(root)
    canonical = contract.canonical_json(proof).encode("ascii")
    return {"receipt_format": authority_mod.RECEIPT_FORMAT, "proof": proof,
            "proof_sha256": hashlib.sha256(canonical).hexdigest()}


def _write_inputs(root: Path, *, policy: dict | None = None,
                  receipt: dict | None = None) -> None:
    policy_path = root / authority_mod.POLICY_RELATIVE
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(json.dumps(policy if policy is not None else _policy()))
    proof_path = root / authority_mod.STOP_PROOF_RELATIVE
    proof_path.parent.mkdir(parents=True, exist_ok=True)
    proof_path.write_text(json.dumps(receipt if receipt is not None else _receipt(root)))


class PackagedRegistryTests(unittest.TestCase):
    def _staged(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="gate-registry-"))
        self.addCleanup(shutil.rmtree, root, True)
        shutil.copytree(PACKAGING, root / "packaging" / "developer-gates")
        for relative in RUNNER_FILES:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        return root

    def test_loads_exact_packaged_bytes_and_frozen_schema(self) -> None:
        registry = load_packaged_registry()
        self.assertEqual(len(registry.gates), 1)
        gate = registry.gates[0]
        tool_manifest = gate["tool_manifest"]

        self.assertEqual(gate["sha256"], _sha(PACKAGING / "library-portable.gate.json"))
        self.assertEqual(gate["command_fixture_sha256"],
                         _sha(PACKAGING / "library-portable.commands.json"))
        self.assertEqual(gate["dependency_manifest_sha256"],
                         _sha(PACKAGING / "library-portable.dependency-manifest.json"))
        self.assertEqual(tool_manifest["sha256"], _sha(PACKAGING / "official-tools.json"))
        self.assertEqual(gate["source"], {
            "sha256": "103a4d5d882726c035f12e5d0b21587d6d8f2da74c51f4583c25e5c41cf6a8b2",
            "size_bytes": 1689600,
            "expanded_bytes": 1496064,
            "file_count": 225,
            "commit": "d9c950547cb119c6aa57c54f9e2014a678593692",
            "media_type": "application/x-tar",
        })
        self.assertEqual(gate["required_toolchains"], tool_manifest["tools"])
        self.assertEqual({pin["id"]: pin["version"] for pin in gate["required_toolchains"]},
                         {"go": "1.27.2", "node": "26.8.2", "python": "3.15.0"})
        self.assertEqual(gate["check_ids"], [
            "signing-verifier-unit", "javascript-unit", "vet-darwin",
            "vet-windows", "vet-linux", "go-unit",
        ])
        self.assertEqual(gate["budget_provenance"], "requested")

        contract.validate_schema(gate, "gate")
        contract.validate_schema(tool_manifest, "toolManifest")
        self.assertEqual(registry.runner, {
            "id": gate_runner.RUNNER_ID,
            "revision": gate_runner.RUNNER_REVISION,
            "sha256": _sha(ROOT / RUNNER_FILES[0]),
        })
        self.assertFalse(registry.supported)
        self.assertNotIn("stop_proof", gate)
        self.assertNotIn("stop_proof", registry.__dict__)

    def test_fixture_disk_floor_is_preserved_not_replaced_by_owner_floor(self) -> None:
        gate = load_packaged_registry().gates[0]
        fixture = json.loads((PACKAGING / "library-portable.commands.json").read_text())
        self.assertEqual(fixture["executionRequirements"]["minimumDiskBytes"], 40_000_000_000)
        self.assertEqual(gate["limits"]["workspace_bytes"], 40_000_000_000)
        self.assertEqual(gate["limits"]["minimum_free_bytes"], 10 * 1024 ** 3)
        self.assertNotEqual(gate["limits"]["minimum_free_bytes"],
                            gate["limits"]["workspace_bytes"])

    def test_shared_cache_cap_matches_packaged_download_cache(self) -> None:
        gate = load_packaged_registry().gates[0]
        official = json.loads((PACKAGING / "official-tools.json").read_text())
        self.assertEqual(gate["limits"]["shared_cache_bytes"], 536870912)
        self.assertGreater(gate["limits"]["shared_cache_bytes"], 0)
        self.assertEqual(gate["limits"]["shared_cache_bytes"],
                         official["limits"]["download_cache_bytes"])

    def test_staged_relocation_returns_identical_descriptors(self) -> None:
        self.assertEqual(load_packaged_registry(self._staged()), load_packaged_registry())

    def test_tampered_packaged_bytes_are_rejected(self) -> None:
        for relative in PACKAGED_FILES:
            if relative.endswith("library-portable.registry.json"):
                continue  # the registry is the trusted root, not a pinned child
            with self.subTest(relative=relative):
                root = self._staged()
                target = root / relative
                target.write_bytes(target.read_bytes() + b" ")
                with self.assertRaises(RegistryError):
                    load_packaged_registry(root)

    def test_missing_packaged_inputs_are_rejected(self) -> None:
        for relative in PACKAGED_FILES:
            with self.subTest(relative=relative):
                root = self._staged()
                (root / relative).unlink()
                with self.assertRaises(RegistryError):
                    load_packaged_registry(root)

    def test_gate_definition_cannot_contain_its_own_hash(self) -> None:
        root = self._staged()
        gate_path = root / "packaging/developer-gates/library-portable.gate.json"
        gate = json.loads(gate_path.read_text())
        gate["sha256"] = "1" * 64
        gate_path.write_text(json.dumps(gate))
        registry_path = root / "packaging/developer-gates/library-portable.registry.json"
        registry = json.loads(registry_path.read_text())
        registry["gate"]["sha256"] = _sha(gate_path)
        registry_path.write_text(json.dumps(registry))
        with self.assertRaises(RegistryError) as caught:
            load_packaged_registry(root)
        self.assertIn("own sha256", str(caught.exception))

    def test_zero_placeholder_runner_hash_is_rejected(self) -> None:
        root = self._staged()
        registry_path = root / "packaging/developer-gates/library-portable.registry.json"
        registry = json.loads(registry_path.read_text())
        registry["runner"]["files"][0]["sha256"] = "0" * 64
        registry_path.write_text(json.dumps(registry))
        with self.assertRaises(RegistryError) as caught:
            load_packaged_registry(root)
        self.assertIn("real 64-character", str(caught.exception))


class PackagedAuthorityTests(unittest.TestCase):
    """Synthetic receipts exercise validation only; they are not live stop proof."""

    def _root(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="gate-authority-"))
        self.addCleanup(shutil.rmtree, root, True)
        shutil.copytree(PACKAGING, root / "packaging" / "developer-gates")
        for relative in RUNNER_FILES:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        copied = root / authority_mod.STOP_PROOF_RELATIVE
        if copied.exists():
            copied.chmod(0o644)  # the packaged receipt is read-only; tests replace it
        _write_inputs(root)
        return root

    def test_verified_receipt_and_operator_policy_produce_frozen_authority(self) -> None:
        root = self._root()
        loaded = authority_mod.load_packaged_authority(root, NODE, CONTROLLER, root=root)
        proof = loaded.stop_proof
        self.assertEqual(set(proof), {"revision", "proof_sha256", "stop_latency_seconds"})
        self.assertEqual(proof["revision"], "runner-entry-stop.1")
        self.assertEqual(proof["stop_latency_seconds"], 0.073)
        self.assertEqual(proof["proof_sha256"],
                         _receipt(root)["proof_sha256"])
        self.assertEqual(loaded.policy["minimum_internal_physical_bytes"],
                         500_000_000_000)
        self.assertEqual(loaded.runner["id"], gate_runner.RUNNER_ID)
        self.assertEqual(len(loaded.gates), 1)
        self.assertEqual({item["path"] for item in
                          load_packaged_registry(root).runner_files},
                         set(authority_mod._SOURCE_FILES.values()))

    def test_each_unverified_input_keeps_authority_unsupported(self) -> None:
        def receipt_with(mutate):
            proof = _proof(root)
            mutate(proof)
            return _receipt(root, proof)

        root = self._root()
        cases = {
            "proof hash": _receipt(root) | {"proof_sha256": "1" * 64},
            "stale source": _receipt(root, {
                **_proof(root),
                "sources": {key: {"sha256": "2" * 64} for key in authority_mod._SOURCE_FILES},
            }),
            "unconfirmed release": receipt_with(
                lambda proof: proof["scenarios"][0].update(
                    process_release={"confirmed": False, "owned_processes_remaining": 0})),
            "owned process remains": receipt_with(
                lambda proof: proof["scenarios"][1].update(
                    process_release={"confirmed": True, "owned_processes_remaining": 1})),
            "unknown identity": receipt_with(
                lambda proof: proof["scenarios"][0].update(checked_identities=[
                    {"pid": 4321, "create_time": 1699999999.0, "alive_after": True}])),
            "no recorded identity": receipt_with(
                lambda proof: proof["scenarios"][0].update(checked_identities=[])),
            "platform": receipt_with(
                lambda proof: proof.update(host={"os": "linux", "arch": "x86_64"})),
            "string created_at": receipt_with(
                lambda proof: proof.update(created_at="2026-11-08T00:00:00Z")),
            "zero pid": receipt_with(
                lambda proof: proof["scenarios"][0].update(
                    checked_identities=[{"pid": 0, "create_time": 1699999999.0,
                                         "alive_after": False}])),
            "non-string scenario name": receipt_with(
                lambda proof: proof["scenarios"][0].update(name=["first-check-exit"])),
        }
        for name, receipt in cases.items():
            with self.subTest(name=name):
                case_root = self._root()
                _write_inputs(case_root, receipt=receipt)
                with self.assertRaises(authority_mod.AuthorityError):
                    authority_mod.load_packaged_authority(
                        case_root, NODE, CONTROLLER, root=case_root)

    def test_policy_mismatch_symlink_and_absence_fail_closed(self) -> None:
        cases = {
            "pair not allowed": _policy(allowed=[]),
            "pair excluded": _policy(excluded=[{"controller_id": CONTROLLER,
                                                "node_id": NODE}]),
            "wrong node": _policy(allowed=[{"controller_id": CONTROLLER, "node_id": "other"}]),
        }
        for name, policy in cases.items():
            with self.subTest(name=name):
                root = self._root()
                _write_inputs(root, policy=policy)
                with self.assertRaises(authority_mod.AuthorityError):
                    authority_mod.load_packaged_authority(root, NODE, CONTROLLER, root=root)

        root = self._root()
        policy_path = root / authority_mod.POLICY_RELATIVE
        real = root / "real-policy.json"
        policy_path.replace(real)
        os.symlink(real, policy_path)
        with self.assertRaises(authority_mod.AuthorityError):
            authority_mod.load_packaged_authority(root, NODE, CONTROLLER, root=root)

        root = self._root()
        (root / authority_mod.POLICY_RELATIVE).unlink()
        with self.assertRaises(authority_mod.AuthorityError):
            authority_mod.load_packaged_authority(root, NODE, CONTROLLER, root=root)

    def test_policy_resolves_at_the_state_root_not_a_nested_directory(self) -> None:
        root = self._root()
        nested = root / "developer-gates" / "policy.json"
        nested.parent.mkdir()
        nested.write_text(json.dumps(_policy()))
        (root / authority_mod.POLICY_RELATIVE).unlink()
        with self.assertRaises(authority_mod.AuthorityError):
            authority_mod.load_packaged_authority(root, NODE, CONTROLLER, root=root)

    def test_missing_stop_proof_is_unsupported(self) -> None:
        root = self._root()
        (root / authority_mod.STOP_PROOF_RELATIVE).unlink()
        with self.assertRaises(authority_mod.AuthorityError):
            authority_mod.load_packaged_authority(root, NODE, CONTROLLER, root=root)


if __name__ == "__main__":
    unittest.main()
