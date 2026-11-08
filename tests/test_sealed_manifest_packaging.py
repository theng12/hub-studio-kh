"""Focused packaging tests for code-signed bundles with sidecar manifests.

Verifies:
  1. Actual Hub manifest producer against the sealed-manifest contract fixture.
  2. Real Mach-O shared-identity inside-out code signing with post-seal sidecar manifest.
  3. Strict codesign deep verification exits 0 on sealed bundle.
  4. Native Mach-O and CodeResources closure in sidecar manifest.
  5. Tampered Mach-O, CodeResources, or sidecar rejected by validate_stage.
  6. Legacy embedded file-manifest refused by validate_stage.
  7. DMG build copies sidecar to volume root while preserving strict codesign seal.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests/fixtures/sealed_manifest/sealed_manifest_fixture.json"
SYSTEM_MACHO = Path("/usr/bin/true")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SealedManifestPackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stage_voice = load_module("test_stage_voice", ROOT / "packaging/stage_voice.py")
        cls.make_dmg = load_module("test_make_dmg", ROOT / "packaging/make_dmg.py")
        cls.file_manifest = load_module("test_file_manifest", ROOT / "components/hub/backend/file_manifest.py")

    def test_actual_producer_against_contract_fixture(self):
        """Hub's actual file_manifest.scan and build match the contract fixture exactly."""
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        valid = fixture["valid_sealed_release"]
        app_files = valid["app_files"]
        expected_hashes = valid["hashes"]

        with tempfile.TemporaryDirectory(prefix="hub-producer-fixture-") as temp_dir:
            app_dir = Path(temp_dir) / "Studio Hub.app"
            for rel, info in app_files.items():
                dest = app_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(base64.b64decode(info["content_b64"]))
                if info["executable"]:
                    os.chmod(dest, 0o755)

            # Measure with actual Hub scanner and builder
            scanned = self.file_manifest.scan(app_dir)
            index, tsv = self.file_manifest.build(scanned)

            self.assertEqual(index["schema"], "studiohub.file-manifest.v1")
            self.assertEqual(index["files_sha256"], expected_hashes["files_sha256"])
            self.assertEqual(index["files_count"], 4)
            self.assertEqual(
                index["groups"]["core"]["sha256"],
                expected_hashes["core_uncompressed_tsv_sha256"],
            )
            self.assertEqual(
                tsv["core"].decode("utf-8"),
                valid["manifest_tsv"]["core"],
            )

            # Check index validation and group verification
            checked_index = self.file_manifest.check_index(index)
            self.assertEqual(checked_index["files_sha256"], expected_hashes["files_sha256"])
            entries = self.file_manifest.check_group(index, "core", tsv["core"])
            self.assertEqual(len(entries), 4)

    def _build_real_app(self, temp_dir: Path) -> Path:
        """Create an unsigned minimal staged bundle using a real system Mach-O binary."""
        app = temp_dir / "Studio Hub.app"
        macos_dir = app / "Contents/MacOS"
        macos_dir.mkdir(parents=True)
        # Copy a real tiny system Mach-O binary
        shutil.copyfile(SYSTEM_MACHO, macos_dir / "StudioHub")
        (macos_dir / "StudioHub").chmod(0o755)

        info_plist = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>'
            '<key>CFBundleExecutable</key><string>StudioHub</string>'
            '<key>CFBundleIdentifier</key><string>local.studiohub.test</string>'
            '<key>CFBundleName</key><string>Studio Hub</string>'
            '<key>CFBundleShortVersionString</key><string>0.6.225</string>'
            '</dict></plist>\n'
        )
        (app / "Contents/Info.plist").write_text(info_plist)
        (app / "Contents/Resources").mkdir(parents=True)
        (app / "Contents/Resources/version.txt").write_text("0.6.225\n")
        return app

    def _create_real_signed_app(self, temp_dir: Path) -> Path:
        """Stage order: sign, post-sign payload, manifest, re-sign, sidecar, tree."""
        app = self._build_real_app(temp_dir)
        contents = app / "Contents"
        exclude = {Path("Resources/release-manifest.json"), Path("_CodeSignature")}
        self.stage_voice.sign_app(app)
        payload = self.stage_voice.payload_digest(contents, exclude=exclude)
        (contents / "Resources/release-manifest.json").write_text(
            json.dumps({"schema": "studiohub.standalone-release",
                        "payload_sha256": payload}))
        self.stage_voice.sign_app(app)
        # Recorded-vs-final payload stability asserted on the actual Mach-O, never assumed
        self.assertEqual(payload, self.stage_voice.payload_digest(contents, exclude=exclude))
        self.stage_voice.write_file_manifest(app)
        app.with_name(app.name + ".tree-sha256").write_text(
            self.stage_voice.tree_digest(app) + "\n"
        )
        return app

    def test_payload_sha256_is_postsign_deterministic_and_seal_stays_valid(self):
        """payload_sha256 is the POST-SIGN digest (Developer 2026-11-07): sign, digest
        over signed Contents excluding release-manifest.json and _CodeSignature with
        LC_CODE_SIGNATURE blobs masked, write the manifest, re-sign once; recorded payload
        must equal the final recomputation even though the re-seal rewrites the Mach-O's
        signature bytes, and the digest must still move when real code bytes move."""
        source = (ROOT / "packaging/stage_voice.py").read_text()
        first_sign = source.index("sign_app(output)")
        payload_at = source.index("release['payload_sha256']")
        manifest_at = source.index("release-manifest.json').write_text")
        second_sign = source.index("sign_app(output)", payload_at)
        tree_at = source.index(".tree-sha256').write_text")
        self.assertLess(first_sign, payload_at)
        self.assertLess(payload_at, manifest_at)
        self.assertLess(manifest_at, second_sign)
        self.assertLess(second_sign, tree_at)

        with tempfile.TemporaryDirectory(prefix="payload-postsign-") as temp_dir:
            app = self._build_real_app(Path(temp_dir))
            contents = app / "Contents"
            native = contents / "MacOS/StudioHub"
            exclude = {Path("Resources/release-manifest.json"), Path("_CodeSignature")}
            native_before = hashlib.sha256(native.read_bytes()).hexdigest()

            self.stage_voice.sign_app(app)  # initial sign
            native_signed = hashlib.sha256(native.read_bytes()).hexdigest()
            # the seal rewrote the real Mach-O: fake-byte fixtures never exercise this
            self.assertNotEqual(native_before, native_signed)

            payload = self.stage_voice.payload_digest(contents, exclude=exclude)
            (contents / "Resources/release-manifest.json").write_text(
                json.dumps({"schema": "studiohub.standalone-release",
                            "payload_sha256": payload}))
            self.stage_voice.sign_app(app)  # re-sign once: seal covers the manifest
            native_resent = hashlib.sha256(native.read_bytes()).hexdigest()
            # full re-sign byte determinism is FALSE on a real Mach-O: codesign seals the
            # manifest into the principal's CodeDirectory, so the re-seal churns those bytes
            self.assertNotEqual(native_signed, native_resent)
            # ... yet the recorded payload must equal the final recomputation
            final = self.stage_voice.payload_digest(contents, exclude=exclude)
            self.assertEqual(payload, final)

            verify = subprocess.run(
                ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                capture_output=True, text=True)
            self.assertEqual(verify.returncode, 0, verify.stderr)
            self.stage_voice.write_file_manifest(app)
            app.with_name(app.name + ".tree-sha256").write_text(
                self.stage_voice.tree_digest(app) + "\n")
            self.assertEqual(
                app.with_name(app.name + ".tree-sha256").read_text().strip(),
                self.stage_voice.tree_digest(app))
            index, groups = self.file_manifest.load_dir(
                app.with_name(app.name + ".file-manifest"))
            self.assertEqual(self.file_manifest.flatten(groups),
                             self.file_manifest.scan(app))
            self.assertRegex(payload, r"^[0-9a-f]{64}$")

            # a file added after the final seal invalidates it (no third sign exists)
            (contents / "Resources/extra-post-seal.txt").write_text("late")
            late = subprocess.run(
                ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                capture_output=True, text=True)
            self.assertNotEqual(late.returncode, 0)

            # anti-masking proof: mutating real Mach-O CODE bytes (outside the signature
            # blob) must move the recorded digest - never a fake-byte-only success
            content = bytearray(native.read_bytes())
            content[0x200] ^= 0x55
            native.write_bytes(bytes(content))
            self.assertNotEqual(payload, self.stage_voice.payload_digest(
                contents, exclude=exclude))

    def test_mutate_release_metadata_rejected(self):
        """Tampering with the sealed release-manifest.json fails validate_stage."""
        with tempfile.TemporaryDirectory(prefix="mutate-metadata-") as temp_dir:
            app = self._create_real_signed_app(Path(temp_dir))
            (app / "Contents/Resources/release-manifest.json").write_text(
                '{"tampered": true}\n')
            with self.assertRaises(ValueError):
                self.make_dmg.validate_stage(app)

    def test_nested_macho_is_signed_inside_out_with_shared_identity(self):
        """Nested Mach-O is signed before the outer seal, and signing is never `--deep`."""
        source = (ROOT / "packaging/stage_voice.py").read_text()
        self.assertIn("SIGNING_IDENTITY = 'KH Studio Local Signing'", source)
        self.assertNotIn("'--deep', '--sign'", source)

        with tempfile.TemporaryDirectory(prefix="nested-sign-") as temp_dir:
            app = self._build_real_app(Path(temp_dir))
            nested = app / "Contents/Resources/nested.dylib"
            shutil.copyfile(SYSTEM_MACHO, nested)
            self.stage_voice.sign_app(app)
            for path in (nested, app):
                described = subprocess.run(
                    ["/usr/bin/codesign", "-d", "--verbose=4", str(path)],
                    capture_output=True, text=True)
                self.assertEqual(described.returncode, 0, described.stderr)
                self.assertIn("Authority=KH Studio Local Signing", described.stderr)
            verify = subprocess.run(
                ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                capture_output=True, text=True)
            self.assertEqual(verify.returncode, 0, verify.stderr)

    def test_copied_executable_authority_is_not_the_app_identity(self):
        """An unsealed app inherits its main executable's Authority; never preserve that."""
        with tempfile.TemporaryDirectory(prefix="copied-authority-") as temp_dir:
            app = self._build_real_app(Path(temp_dir))
            self.assertFalse((app / "Contents/_CodeSignature/CodeResources").exists())
            self.assertIsNone(self.stage_voice._existing_real_identity(app))

    def test_real_macho_shared_identity_signing_and_sidecar_closure(self):
        """Prove post-seal manifest app strict deep is 0 and every actual signed file indexed."""
        with tempfile.TemporaryDirectory(prefix="macho-seal-test-") as temp_dir:
            app = self._create_real_signed_app(Path(temp_dir))

            # 1. codesign --verify --deep --strict exits 0
            verify = subprocess.run(
                ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                capture_output=True, text=True,
            )
            self.assertEqual(verify.returncode, 0, f"codesign failed: {verify.stderr}")

            # 2. No file-manifest inside the app bundle
            self.assertFalse((app / "Contents/Resources/file-manifest").exists())

            # 3. Sidecar directory exists alongside app
            sidecar = app.with_name(app.name + ".file-manifest")
            self.assertTrue(sidecar.is_dir())
            self.assertTrue((sidecar / "index.json").is_file())
            self.assertTrue((sidecar / "core.tsv.gz").is_file())

            # 4. Native executable and CodeResources indexed with closure
            index, groups = self.file_manifest.load_dir(sidecar)
            flattened = self.file_manifest.flatten(groups)
            self.assertIn("Contents/MacOS/StudioHub", flattened)
            self.assertIn("Contents/_CodeSignature/CodeResources", flattened)
            self.assertEqual(flattened["Contents/MacOS/StudioHub"][0], "f")
            self.assertTrue(flattened["Contents/MacOS/StudioHub"][1])  # executable

            # 5. validate_stage passes cleanly
            self.make_dmg.validate_stage(app)

    def test_mutate_native_macho_rejected(self):
        """Mutating signed Mach-O fails codesign verify and validate_stage."""
        with tempfile.TemporaryDirectory(prefix="mutate-native-") as temp_dir:
            app = self._create_real_signed_app(Path(temp_dir))
            # Mutate native binary
            native = app / "Contents/MacOS/StudioHub"
            content = bytearray(native.read_bytes())
            content[0x200] ^= 0x55
            native.write_bytes(bytes(content))

            # codesign fails
            ver = subprocess.run(
                ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                capture_output=True, text=True,
            )
            self.assertNotEqual(ver.returncode, 0)

            # validate_stage fails
            with self.assertRaises(ValueError):
                self.make_dmg.validate_stage(app)

    def test_mutate_code_signature_rejected(self):
        """Mutating Contents/_CodeSignature/CodeResources fails validate_stage."""
        with tempfile.TemporaryDirectory(prefix="mutate-sig-") as temp_dir:
            app = self._create_real_signed_app(Path(temp_dir))
            sig = app / "Contents/_CodeSignature/CodeResources"
            self.assertTrue(sig.is_file())
            sig.write_text("<plist>tampered</plist>\n")

            ver = subprocess.run(
                ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                capture_output=True, text=True,
            )
            self.assertNotEqual(ver.returncode, 0)

            with self.assertRaises(ValueError):
                self.make_dmg.validate_stage(app)

    def test_mutate_sidecar_manifest_rejected(self):
        """Tampering with sidecar manifest index or group fails validate_stage."""
        with tempfile.TemporaryDirectory(prefix="mutate-sidecar-") as temp_dir:
            app = self._create_real_signed_app(Path(temp_dir))
            sidecar = app.with_name(app.name + ".file-manifest")
            index_file = sidecar / "index.json"
            data = json.loads(index_file.read_text())
            data["files_sha256"] = "0" * 64
            index_file.write_text(json.dumps(data))

            with self.assertRaises(ValueError):
                self.make_dmg.validate_stage(app)

    def test_legacy_embedded_manifest_refused(self):
        """App bundle with legacy embedded file-manifest is refused."""
        with tempfile.TemporaryDirectory(prefix="legacy-embedded-") as temp_dir:
            app = self._create_real_signed_app(Path(temp_dir))
            embedded = app / "Contents/Resources/file-manifest"
            embedded.mkdir(parents=True)
            (embedded / "index.json").write_text("{}")

            with self.assertRaisesRegex(ValueError, "embedded file-manifest"):
                self.make_dmg.validate_stage(app)

    def test_build_dmg_copies_sidecar_to_volume_root(self):
        """make_dmg.build copies sidecar to DMG root and preserves code signature seal."""
        with tempfile.TemporaryDirectory(prefix="dmg-build-test-") as temp_dir:
            td = Path(temp_dir)
            app = self._create_real_signed_app(td)

            dist_dir = ROOT / "dist"
            dist_dir.mkdir(parents=True, exist_ok=True)
            dmg_path = dist_dir / "Studio Hub Test 0.6.225.dmg"

            try:
                self.make_dmg.build(app, dmg_path)
                self.assertTrue(dmg_path.is_file())

                # Mount read-only and verify volume layout
                mount = Path(tempfile.mkdtemp(prefix="shub-dmg-mount-"))
                try:
                    subprocess.run(
                        ["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse",
                         "-noautoopen", "-mountpoint", str(mount), str(dmg_path)],
                        check=True, capture_output=True, text=True,
                    )
                    mounted_app = mount / "Studio Hub.app"
                    mounted_sidecar = mount / "file-manifest"

                    self.assertTrue(mounted_app.is_dir())
                    self.assertTrue(mounted_sidecar.is_dir())
                    self.assertTrue((mounted_sidecar / "index.json").is_file())
                    self.assertFalse((mounted_app / "Contents/Resources/file-manifest").exists())

                    # Strict deep codesign verify on mounted app MUST be 0
                    ver = subprocess.run(
                        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(mounted_app)],
                        capture_output=True, text=True,
                    )
                    self.assertEqual(ver.returncode, 0, f"mounted codesign failed: {ver.stderr}")

                    # Sidecar closure on mounted bundle
                    index, groups = self.file_manifest.load_dir(mounted_sidecar)
                    flattened = self.file_manifest.flatten(groups)
                    scanned = self.file_manifest.scan(mounted_app)
                    self.assertEqual(flattened, scanned)
                finally:
                    subprocess.run(["/usr/bin/hdiutil", "detach", str(mount)], capture_output=True)
                    if mount.is_dir():
                        mount.rmdir()
            finally:
                if dmg_path.exists():
                    dmg_path.unlink()


if __name__ == "__main__":
    unittest.main()
