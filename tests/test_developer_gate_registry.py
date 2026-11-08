"""Tests for the packaged Library portable developer-gate registry loader.

Covers exact raw-byte identity binding, frozen schema validation, tamper rejection,
missing packaged inputs, self-referential hash refusal, zero placeholder refusal and a
staged relocation of the same layout. No Hub start, network, build or real library.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub import developer_gate_contract as contract
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


if __name__ == "__main__":
    unittest.main()
