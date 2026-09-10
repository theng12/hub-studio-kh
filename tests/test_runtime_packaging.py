import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGER = PROJECT_ROOT / "packaging" / "voice-runtime" / "runtime_packaging.py"


def load_packager():
    spec = importlib.util.spec_from_file_location("runtime_packaging", PACKAGER)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load packaging implementation: {PACKAGER}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except FileNotFoundError as error:
        raise AssertionError(f"packaging implementation is missing: {PACKAGER}") from error
    return module


class RuntimePackagingTests(unittest.TestCase):
    def test_prefix_audit_finds_file_content_and_absolute_symlink(self):
        packaging = load_packager()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "lib").mkdir()
            (root / "lib" / "extension.so").write_bytes(
                b"header\x00/Users/builder/work/runtime\x00footer"
            )
            os.symlink(
                "/Users/builder/work/runtime/bin/python3",
                root / "python-build-link",
            )

            hits = packaging.forbidden_prefix_hits(
                root, ["/Users/builder/work/runtime"]
            )

        self.assertEqual(
            hits,
            [
                {
                    "path": "lib/extension.so",
                    "prefix": "/Users/builder/work/runtime",
                    "source": "contents",
                },
                {
                    "path": "python-build-link",
                    "prefix": "/Users/builder/work/runtime",
                    "source": "symlink",
                },
            ],
        )

    def test_dependency_audit_rejects_non_system_absolute_paths(self):
        packaging = load_packager()

        accepted = [
            "/usr/lib/libSystem.B.dylib",
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
            "@rpath/libpython3.12.dylib",
            "@loader_path/../lib/libexample.dylib",
            "@executable_path/../lib/libexample.dylib",
        ]
        rejected = [
            "/opt/homebrew/opt/libfoo/lib/libfoo.dylib",
            "/Users/builder/work/libbar.dylib",
            "libbare.dylib",
        ]

        self.assertEqual(
            [packaging.invalid_dependency(item) for item in accepted],
            [None] * len(accepted),
        )
        self.assertEqual(
            [packaging.invalid_dependency(item) for item in rejected], rejected
        )

    def test_rpath_audit_rejects_build_host_search_paths(self):
        packaging = load_packager()

        self.assertEqual(
            packaging.invalid_rpath("/Users/runner/build/lib"),
            "/Users/runner/build/lib",
        )
        self.assertIsNone(packaging.invalid_rpath("@loader_path/lib"))
        self.assertIsNone(packaging.invalid_rpath("/usr/lib"))
        self.assertEqual(
            packaging.invalid_install_id("/opt/local/lib/libbuilt.dylib"),
            "/opt/local/lib/libbuilt.dylib",
        )
        self.assertIsNone(packaging.invalid_install_id("@rpath/libbuilt.dylib"))

    def test_macho_detection_uses_magic_bytes_and_universal_can_include_arm64(self):
        packaging = load_packager()
        self.assertTrue(hasattr(packaging, "is_macho"), "magic-byte detector is missing")
        self.assertTrue(hasattr(packaging, "has_arm64_slice"), "arm64 slice check is missing")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arm64 = root / "arm64.dylib"
            universal = root / "universal.dylib"
            text = root / "metadata.txt"
            arm64.write_bytes(bytes.fromhex("cffaedfe") + b"payload")
            universal.write_bytes(bytes.fromhex("cafebabe") + b"payload")
            text.write_text("Mach-O text is not a binary", encoding="utf-8")

            self.assertTrue(packaging.is_macho(arm64))
            self.assertTrue(packaging.is_macho(universal))
            self.assertFalse(packaging.is_macho(text))

        self.assertTrue(packaging.has_arm64_slice(["x86_64", "arm64"]))
        self.assertTrue(packaging.has_arm64_slice(["arm64"]))
        self.assertFalse(packaging.has_arm64_slice(["x86_64"]))

    def test_rpath_dependency_must_resolve_inside_bundle(self):
        packaging = load_packager()
        self.assertTrue(
            hasattr(packaging, "unresolved_relocatable_dependency"),
            "LC_RPATH resolution audit is missing",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "lib" / "python3.12" / "site-packages" / "native.so"
            bundled_library = root / "lib" / "libpython3.12.dylib"
            binary.parent.mkdir(parents=True)
            bundled_library.parent.mkdir(parents=True, exist_ok=True)
            bundled_library.write_bytes(b"library")

            valid = packaging.unresolved_relocatable_dependency(
                root,
                binary,
                "@rpath/libpython3.12.dylib",
                ["@loader_path/../.."],
            )
            invalid = packaging.unresolved_relocatable_dependency(
                root,
                binary,
                "@rpath/libmissing.dylib",
                ["@loader_path/../.."],
            )

        self.assertIsNone(valid)
        self.assertEqual(invalid, "@rpath/libmissing.dylib")

    def test_macos_minimum_parser_handles_both_load_command_formats(self):
        packaging = load_packager()
        modern = """
          cmd LC_BUILD_VERSION
      cmdsize 32
     platform 1
         minos 14.0
           sdk 26.0
"""
        legacy = """
          cmd LC_VERSION_MIN_MACOSX
      cmdsize 16
      version 10.15
          sdk 13.3
"""

        self.assertEqual(packaging.parse_macos_minimum(modern), "14.0")
        self.assertEqual(packaging.parse_macos_minimum(legacy), "10.15")
        self.assertTrue(packaging.minimum_supports_target("14.0", "14.0"))
        self.assertTrue(packaging.minimum_supports_target("14.0.0", "14.0"))
        self.assertFalse(packaging.minimum_supports_target("15.0", "14.0"))

    def test_file_manifest_is_independent_of_bundle_location(self):
        packaging = load_packager()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_root = Path(first)
            second_root = Path(second)
            for root in (first_root, second_root):
                (root / "bin").mkdir()
                (root / "manifest").mkdir()
                executable = root / "bin" / "python3"
                executable.write_bytes(b"portable-python")
                executable.chmod(0o755)
                (root / "manifest" / "runtime-files.json").write_text(
                    "generated manifest is not self-hashed", encoding="utf-8"
                )

            first_manifest = packaging.build_file_manifest(first_root)
            second_manifest = packaging.build_file_manifest(second_root)

        self.assertEqual(first_manifest, second_manifest)
        self.assertEqual(
            first_manifest,
            [
                {
                    "path": "bin/python3",
                    "type": "file",
                    "sha256": "37fbef129866fd27be37207ae0a443d83ada55ddcf7d832f84f2e5c499bb3c91",
                    "size": 15,
                    "mode": "0755",
                }
            ],
        )

    def test_write_manifest_uses_relative_paths_only(self):
        packaging = load_packager()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Runtime With Spaces"
            (root / "bin").mkdir(parents=True)
            (root / "bin" / "python3").write_bytes(b"runtime")

            output = packaging.write_file_manifest(root)
            manifest_text = output.read_text(encoding="utf-8")
            payload = json.loads(manifest_text)

            self.assertEqual(output, root / "manifest" / "runtime-files.json")
        self.assertEqual(payload["files"][0]["path"], "bin/python3")
        self.assertNotIn(str(root), manifest_text)

    def test_manifest_records_links_and_audit_rejects_external_links(self):
        packaging = load_packager()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bin").mkdir()
            (root / "lib").mkdir()
            (root / "lib" / "python3").write_bytes(b"python")
            os.symlink("../lib/python3", root / "bin" / "python3")
            os.symlink("/usr/local/lib/libexternal.dylib", root / "bin" / "external")

            records = packaging.build_file_manifest(root)
            hits = packaging.unsafe_symlink_hits(root)

        link = next(record for record in records if record["path"] == "bin/python3")
        self.assertEqual(link["type"], "symlink")
        self.assertEqual(link["target"], "../lib/python3")
        self.assertEqual(
            hits,
            [
                {
                    "path": "bin/external",
                    "target": "/usr/local/lib/libexternal.dylib",
                    "reason": "absolute",
                }
            ],
        )

    def test_manifest_verification_detects_runtime_mutation(self):
        packaging = load_packager()
        self.assertTrue(
            hasattr(packaging, "verify_file_manifest"),
            "runtime mutation verifier is missing",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bin").mkdir()
            executable = root / "bin" / "python3"
            executable.write_bytes(b"runtime")
            packaging.write_file_manifest(root)

            self.assertEqual(packaging.verify_file_manifest(root), [])
            executable.write_bytes(b"mutated")
            mismatches = packaging.verify_file_manifest(root)

        self.assertEqual(
            mismatches,
            [{"path": "bin/python3", "reason": "sha256"}],
        )

    def test_python_entrypoint_shebangs_become_bundle_relative(self):
        packaging = load_packager()
        self.assertTrue(
            hasattr(packaging, "normalize_python_shebangs"),
            "entrypoint normalizer is missing",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bin").mkdir()
            entrypoint = root / "bin" / "uvicorn"
            entrypoint.write_text(
                "#!/private/build/voice runtime/bin/python3\nprint('server')\n",
                encoding="utf-8",
            )
            entrypoint.chmod(0o755)

            changed = packaging.normalize_python_shebangs(root)
            result = entrypoint.read_text(encoding="utf-8")

        self.assertEqual(changed, ["bin/uvicorn"])
        self.assertTrue(result.startswith("#!/bin/sh\n'''exec' \"$(dirname \"$0\")/python3\""))
        self.assertNotIn("/private/build", result)
        self.assertIn("print('server')", result)


if __name__ == "__main__":
    unittest.main()
