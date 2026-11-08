"""Staging for the developer gate's frozen contract and commands fixture.

The contract module and runner resolve their assets from `Resources/` (two levels above their
staged location), never from the source checkout or the current directory. These tests stage
the exact bytes and import the loaders from a temporary staged layout.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import stage_voice  # noqa: E402

ASSETS = {
    "contracts/developer-gate-v1.schema.json":
        "a97d32a008f62f17293ee0666001ba79207c0367b4424ce6888731f40e66ad9a",
    "contracts/developer-gate-v1-wire.txt":
        "67122d464f591daf34dcaa58664c7e82d8e7b7ef425373ff28aacd5375cf43dd",
    "packaging/developer-gates/library-portable.commands.json":
        "d0b8658d783c7680a96ad0c12526d3ab2ce2ae12c87edb733be20f25cbb3d9df",
    "packaging/developer-gates/official-tools.json":
        "83c6250e19a5cd89e1c34b03702ab1582be78cadb3a1984ff6ea30c1ad239f5c",
}
STAGED_MODULES = ("developer_gate_contract", "developer_gate_runner", "developer_image_runner")


class DeveloperGateStagingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.resources = Path(self._tmp.name) / "Resources"
        stage_voice.stage_developer_gate_assets(self.resources)

    def tearDown(self):
        self._tmp.cleanup()

    def test_staged_assets_keep_exact_bytes_and_hashes(self):
        for relative, expected in ASSETS.items():
            staged = (self.resources / relative).read_bytes()
            self.assertEqual(staged, (ROOT / relative).read_bytes(), relative)
            self.assertEqual(hashlib.sha256(staged).hexdigest(), expected, relative)

    def test_staged_layout_loads_schema_and_fixture_without_source_or_cwd(self):
        hub = self.resources / "components/hub"
        hub.mkdir(parents=True)
        for name in STAGED_MODULES:
            shutil.copyfile(ROOT / "components/hub" / f"{name}.py", hub / f"{name}.py")

        package = types.ModuleType("staged_hub")
        package.__path__ = [str(hub)]
        sys.modules["staged_hub"] = package
        try:
            contract = importlib.import_module("staged_hub.developer_gate_contract")
            runner = importlib.import_module("staged_hub.developer_gate_runner")
            self.assertTrue(Path(contract.__file__).is_relative_to(self.resources))
            schema = contract.load_gate_schema()
            fixture = runner.load_commands_fixture()
            self.assertEqual(
                runner.COMMANDS_FIXTURE_PATH.resolve(),
                (self.resources / "packaging/developer-gates/library-portable.commands.json").resolve())
        finally:
            for name in ("staged_hub.developer_gate_contract", "staged_hub.developer_gate_runner",
                         "staged_hub.developer_image_runner", "staged_hub"):
                sys.modules.pop(name, None)

        self.assertEqual(
            schema,
            json.loads((self.resources / "contracts/developer-gate-v1.schema.json").read_text()))
        self.assertEqual(len(fixture["commands"]), 6)

    def test_staged_official_tools_manifest_loads_from_resource_layout(self):
        hub = self.resources / "components/hub"
        hub.mkdir(parents=True)
        for name in ("developer_gate_contract", "developer_gate_tools"):
            shutil.copyfile(ROOT / "components/hub" / f"{name}.py", hub / f"{name}.py")

        # Import the real modules first; the staged import below must not replace them.
        sys.path.insert(0, str(ROOT))
        try:
            real_tools = importlib.import_module("components.hub.developer_gate_tools")
            real_contract = importlib.import_module("components.hub.developer_gate_contract")
        finally:
            sys.path.remove(str(ROOT))
        real_tools_file = Path(real_tools.__file__).resolve()

        components = types.ModuleType("components")
        components.__path__ = [str(self.resources / "components")]
        hub_package = types.ModuleType("components.hub")
        hub_package.__path__ = [str(hub)]
        names = ("components", "components.hub", "components.hub.developer_gate_contract",
                 "components.hub.developer_gate_tools")
        with mock.patch.dict(sys.modules):
            for name in names:
                sys.modules.pop(name, None)
            sys.modules["components"] = components
            sys.modules["components.hub"] = hub_package
            tools = importlib.import_module("components.hub.developer_gate_tools")
            self.assertTrue(Path(tools.__file__).is_relative_to(self.resources))
            self.assertEqual(
                tools.DEFAULT_MANIFEST_PATH.resolve(),
                (self.resources / "packaging/developer-gates/official-tools.json").resolve())
            manifest = tools.load_official_tools_manifest()

        self.assertEqual(
            manifest,
            json.loads((self.resources / "packaging/developer-gates/official-tools.json").read_text()))
        self.assertIs(sys.modules["components.hub.developer_gate_tools"], real_tools)
        self.assertIs(sys.modules["components.hub.developer_gate_contract"], real_contract)
        self.assertEqual(Path(real_tools.__file__).resolve(), real_tools_file)


if __name__ == "__main__":
    unittest.main()
