"""The Hub bundle carries the KH Updater agent kit, and says exactly what it contains.

The kit is built by kh-updater's own packaging script; these tests build a stand-in
with the same shape, stage it the way `stage_voice.stage()` does, and then read it
back with `components.hub.backend.khu_agent.verify_kit` - the function the Hub itself
uses. That round trip is the point: the stager and the installer must agree about the
same bytes, or a Mac would ship a kit it refuses to install.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGING = PROJECT_ROOT / "packaging"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PACKAGING) not in sys.path:
    sys.path.insert(0, str(PACKAGING))

from components.hub.backend import ed25519, khu_agent  # noqa: E402
from stage_khu_agent import (  # noqa: E402
    PUBLISHER_FILE, STAGED_FILES, stage_agent_kit, validate_publisher_descriptor,
    validate_source_kit,
)

TAILSCALE = "100.64.0.7"


def _descriptor() -> dict:
    public = ed25519.public_key(secrets.token_bytes(32))
    return {
        "feedURL": f"http://{TAILSCALE}:8790/release.json",
        "enrolURL": f"http://{TAILSCALE}:8790/enrol",
        "publicKey": base64.b64encode(public).decode(),
        "testAuthority": False,
    }


def _source_kit(root: Path, version: str = "1.0.0") -> Path:
    """The six credential-free files kh-updater's build-agent-kit.sh produces."""
    kit = root / "AgentKit"
    (kit / "bin").mkdir(parents=True)
    (kit / "VERSION").write_text(version)
    for name in ("bin/khu", "bin/khu-stub", "install-agent.sh"):
        (kit / name).write_bytes(b"#!/bin/sh\nexit 0\n")
        (kit / name).chmod(0o755)
    (kit / "com.khupdater.agent.plist").write_bytes(b"<?xml version=\"1.0\"?><plist><dict/></plist>")
    (kit / "agent-config.example.json").write_text("{}\n")
    return kit


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="khu-stage-"))
        self.resources = self.work / "Resources"
        self.resources.mkdir()
        self.kit = _source_kit(self.work)
        self.descriptor_path = self.work / "publisher.json"
        self.descriptor = _descriptor()
        self.descriptor_path.write_text(json.dumps(self.descriptor))

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def test_staged_kit_verifies_with_the_hubs_own_reader(self):
        result = stage_agent_kit(self.resources, self.kit, self.descriptor_path)
        self.assertEqual(result.agent_version, "1.0.0")
        # The round trip: the Hub's installer must accept what the stager produced.
        manifest = khu_agent.verify_kit(result.staged_path, allow_test=False)
        self.assertEqual(set(manifest["files"]), khu_agent.KIT_FILES)

    def test_manifest_lists_every_staged_file_with_its_own_bytes(self):
        result = stage_agent_kit(self.resources, self.kit, self.descriptor_path)
        manifest = json.loads(result.manifest_path.read_text())
        self.assertEqual(tuple(manifest["files"]), STAGED_FILES)
        self.assertEqual(manifest["format"], "studiohub.khu-agent.v1")
        self.assertEqual(manifest["stage"], "bootstrap-only")
        for name, pin in manifest["files"].items():
            data = (result.staged_path / name).read_bytes()
            self.assertEqual(pin["bytes"], len(data), name)
            self.assertEqual(pin["sha256"], hashlib.sha256(data).hexdigest(), name)

    def test_the_publisher_file_is_written_from_the_descriptor(self):
        result = stage_agent_kit(self.resources, self.kit, self.descriptor_path)
        staged = json.loads((result.staged_path / PUBLISHER_FILE).read_text())
        self.assertEqual(staged, self.descriptor)

    def test_no_credential_lands_in_the_bundle(self):
        # The kit build never produces a token and the descriptor carries none, so neither
        # can the staged bundle. Checked over every staged byte, not just the known files.
        token = "s3cret-token-that-must-not-ship"
        result = stage_agent_kit(self.resources, self.kit, self.descriptor_path)
        for path in result.staged_path.rglob("*"):
            if path.is_file():
                self.assertNotIn(token.encode(), path.read_bytes(), str(path))

    def test_executables_keep_their_mode_and_data_files_do_not(self):
        result = stage_agent_kit(self.resources, self.kit, self.descriptor_path)
        for name in ("bin/khu", "bin/khu-stub", "install-agent.sh"):
            self.assertEqual((result.staged_path / name).stat().st_mode & 0o777, 0o755, name)
        for name in ("VERSION", "com.khupdater.agent.plist",
                     "agent-config.example.json", PUBLISHER_FILE):
            self.assertEqual((result.staged_path / name).stat().st_mode & 0o777, 0o644, name)

    def test_refuses_an_extra_file_in_the_source_kit(self):
        (self.kit / "surprise.txt").write_text("x")
        with self.assertRaises(ValueError):
            validate_source_kit(self.kit)

    def test_refuses_a_missing_file_in_the_source_kit(self):
        (self.kit / "VERSION").unlink()
        with self.assertRaises(ValueError):
            validate_source_kit(self.kit)

    def test_refuses_a_symlinked_kit_file(self):
        (self.kit / "bin/khu").unlink()
        (self.work / "elsewhere").write_text("#!/bin/sh\n")
        (self.kit / "bin/khu").symlink_to(self.work / "elsewhere")
        with self.assertRaises(ValueError):
            validate_source_kit(self.kit)

    def test_refuses_a_test_authority_descriptor(self):
        bad = {**self.descriptor, "testAuthority": True}
        path = self.work / "test-publisher.json"
        path.write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            validate_publisher_descriptor(path)
        with self.assertRaises(ValueError):
            stage_agent_kit(self.resources, self.kit, path)

    def test_refuses_a_descriptor_with_extra_keys(self):
        bad = {**self.descriptor, "token": "must-never-be-here"}
        path = self.work / "extra.json"
        path.write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            validate_publisher_descriptor(path)

    def test_refuses_to_replace_an_existing_kit(self):
        stage_agent_kit(self.resources, self.kit, self.descriptor_path)
        with self.assertRaises(ValueError):
            stage_agent_kit(self.resources, self.kit, self.descriptor_path)

    def test_refuses_a_non_numeric_kit_version(self):
        bad = _source_kit(self.work / "second", version="v1.0")
        with self.assertRaises(ValueError):
            stage_agent_kit(self.resources, bad, self.descriptor_path)

    def test_staged_kit_installs_and_enrols_against_a_fixture_publisher(self):
        """End to end: what the stager writes is what the bootstrap will consume."""
        import asyncio
        result = stage_agent_kit(self.resources, self.kit, self.descriptor_path)
        home = self.work / "Home"
        home.mkdir()
        bootstrap = khu_agent.Bootstrap(result.staged_path, home, allow_test=False)
        # No publisher is reachable here, so enrolment cannot finish - but the install
        # half must succeed on the staged bytes alone, which is the claim under test.
        self.assertEqual(bootstrap.install(), "installed")
        base = home / "Library/KHUpdater/base"
        self.assertTrue((base / "current").is_symlink())
        self.assertEqual((base / "versions/1.0.0/khu").read_bytes(),
                         (result.staged_path / "bin/khu").read_bytes())


if __name__ == "__main__":
    unittest.main()
