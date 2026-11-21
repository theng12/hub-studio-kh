import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


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

    def test_uv_polyglot_shebangs_with_spaces_become_bundle_relative(self):
        packaging = load_packager()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bin").mkdir()
            entrypoint = root / "bin" / "uvicorn"
            entrypoint.write_text(
                "#!/bin/sh\n"
                "'''exec' '/Users/builder/My Own Apps/stage/bin/python3' \"$0\" \"$@\"\n"
                "' '''\n"
                "# -*- coding: utf-8 -*-\n"
                "print('server')\n",
                encoding="utf-8",
            )
            entrypoint.chmod(0o755)
            relative = root / "bin" / "2to3-3.12"
            relative_text = (
                "#!/bin/sh\n"
                "'''exec' \"$(dirname -- \"$(realpath -- \"$0\")\")/python3.12\" \"$0\" \"$@\"\n"
                "' '''\n"
                "import sys\n"
            )
            relative.write_text(relative_text, encoding="utf-8")

            changed = packaging.normalize_python_shebangs(root)
            result = entrypoint.read_text(encoding="utf-8")
            untouched = relative.read_text(encoding="utf-8")
            second_pass = packaging.normalize_python_shebangs(root)

        self.assertEqual(changed, ["bin/uvicorn"])
        self.assertEqual(second_pass, [])
        self.assertTrue(result.startswith("#!/bin/sh\n'''exec' \"$(dirname \"$0\")/python3\""))
        self.assertNotIn("/Users/builder", result)
        self.assertIn("print('server')", result)
        self.assertEqual(untouched, relative_text)


class RuntimeTestOnlyContentTests(unittest.TestCase):
    """Nothing that exists only to run tests may ship inside the Voice runtime.

    0.6.200's DMG carried every wheel's `tests/` directory plus pytest 9.1.1, pluggy and
    iniconfig - installed into `.build/voice-runtime` by hand and copied into the bundle
    wholesale. These pin both halves: the sweep that removes them, and the refusal that stops a
    tree which still has them from being shipped.
    """

    def staged_runtime(self, root: Path) -> Path:
        packages = root / "lib" / "python3.12" / "site-packages"
        files = {
            # A wheel's own test tree, and a package's public `testing` API beside it.
            "numpy/tests/test_basic.py": "def test_it():\n    pass\n",
            "numpy/testing/__init__.py": "def assert_equal(left, right):\n    assert left == right\n",
            "numpy/conftest.py": "collect_ignore = []\n",
            # scipy implements `page_trend_test` and `bws_test` in `_`-prefixed modules.
            "scipy/stats/_page_trend_test.py": "def page_trend_test():\n    pass\n",
            "scipy/stats/tests/test_stats.py": "def test_mean():\n    pass\n",
            # mpmath's own `runtests()` imports its test package, so that one stays.
            "mpmath/__init__.py": "def runtests():\n    from .tests import runtests as tests\n",
            "mpmath/tests/__init__.py": "",
            "mpmath/tests/runtests.py": "def testit(importdir, testdir):\n    pass\n",
            "mpmath/tests/test_basic.py": "def test_sqrt():\n    pass\n",
            # Jinja's test vocabulary is a runtime module called `tests`.
            "jinja2/defaults.py": "from .tests import TESTS as DEFAULT_TESTS\n",
            "jinja2/tests.py": "TESTS = {}\n",
            # A vendored test module that is not in a `tests` directory.
            "cn2an/transform_test.py": "def test_transform():\n    pass\n",
            # torch's Dynamo imports this one at run time; removing it broke `AutoTokenizer`
            # (and with it Whisper transcription) when only the filename was consulted.
            "torch/_dynamo/trace_rules.py": "import torch\nimport torch._inductor.test_operators\n",
            "torch/_inductor/test_operators.py": "def op():\n    pass\n",
            # pytest and its friends, as a hand-run `pip install pytest` leaves them.
            "pytest/__init__.py": "from _pytest import main\n",
            "pytest-9.1.1.dist-info/METADATA": "Name: pytest\n",
            "_pytest/main.py": "def console_main():\n    pass\n",
            "pluggy/__init__.py": "",
            "pluggy-1.7.0.dist-info/METADATA": "Name: pluggy\n",
            "iniconfig/__init__.py": "",
            "pygments/__init__.py": "# not a test framework\n",
        }
        for name, contents in files.items():
            path = packages / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")
        (root / "bin").mkdir(parents=True, exist_ok=True)
        (root / "bin" / "python3").write_bytes(b"runtime")
        for script in ("pytest", "py.test", "uvicorn"):
            entrypoint = root / "bin" / script
            entrypoint.write_text("#!/bin/sh\n", encoding="utf-8")
            entrypoint.chmod(0o755)
        return root

    def test_sweep_removes_wheel_tests_and_test_frameworks_and_keeps_runtime_modules(self):
        packaging = load_packager()
        with tempfile.TemporaryDirectory() as temporary:
            root = self.staged_runtime(Path(temporary))
            removed = sorted(packaging.strip_test_only(root)["removed"])
            packages = root / "lib" / "python3.12" / "site-packages"
            survivors = sorted(
                path.relative_to(root).as_posix()
                for path in packages.rglob("*")
                if path.is_file()
            )

        self.assertEqual(
            removed,
            [
                "bin/py.test",
                "bin/pytest",
                "lib/python3.12/site-packages/_pytest",
                "lib/python3.12/site-packages/cn2an/transform_test.py",
                "lib/python3.12/site-packages/iniconfig",
                "lib/python3.12/site-packages/numpy/conftest.py",
                "lib/python3.12/site-packages/numpy/tests",
                "lib/python3.12/site-packages/pluggy",
                "lib/python3.12/site-packages/pluggy-1.7.0.dist-info",
                "lib/python3.12/site-packages/pytest",
                "lib/python3.12/site-packages/pytest-9.1.1.dist-info",
                "lib/python3.12/site-packages/scipy/stats/tests",
            ],
        )
        # The three runtime modules that merely look like tests, and an unrelated dependency.
        self.assertIn("lib/python3.12/site-packages/mpmath/tests/runtests.py", survivors)
        self.assertIn("lib/python3.12/site-packages/mpmath/tests/test_basic.py", survivors)
        self.assertIn("lib/python3.12/site-packages/scipy/stats/_page_trend_test.py", survivors)
        self.assertIn("lib/python3.12/site-packages/jinja2/tests.py", survivors)
        self.assertIn("lib/python3.12/site-packages/numpy/testing/__init__.py", survivors)
        self.assertIn("lib/python3.12/site-packages/pygments/__init__.py", survivors)
        self.assertIn("lib/python3.12/site-packages/torch/_inductor/test_operators.py", survivors)
        self.assertNotIn("lib/python3.12/site-packages/numpy/tests/test_basic.py", survivors)
        self.assertNotIn("lib/python3.12/site-packages/cn2an/transform_test.py", survivors)

    def test_sweep_rewrites_the_manifest_so_it_does_not_describe_removed_files(self):
        packaging = load_packager()
        with tempfile.TemporaryDirectory() as temporary:
            root = self.staged_runtime(Path(temporary))
            packaging.write_file_manifest(root)
            manifest = root / "manifest" / "runtime-files.json"
            before = manifest.read_text(encoding="utf-8")
            # The build stage seals the manifest read-only; rewriting it must not unseal it.
            manifest.chmod(0o444)
            manifest.parent.chmod(0o555)

            packaging.strip_test_only(root)
            after = manifest.read_text(encoding="utf-8")

            self.assertEqual(packaging.verify_file_manifest(root), [])
            self.assertNotIn("numpy/tests/test_basic.py", after)
            self.assertEqual(manifest.stat().st_mode & 0o777, 0o444)
            self.assertEqual(manifest.parent.stat().st_mode & 0o777, 0o555)
            manifest.parent.chmod(0o755)
            manifest.chmod(0o644)

        self.assertIn("numpy/tests/test_basic.py", before)

    def test_sweep_works_on_a_read_only_staged_runtime(self):
        packaging = load_packager()
        with tempfile.TemporaryDirectory() as temporary:
            root = self.staged_runtime(Path(temporary))
            for path in [root, *root.rglob("*")]:
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode & ~0o222)
            try:
                removed = packaging.strip_test_only(root)["removed"]
                self.assertEqual(packaging.test_only_hits(root), [])
            finally:
                for path in [root, *root.rglob("*")]:
                    if not path.is_symlink():
                        path.chmod(path.stat().st_mode | 0o200)

        self.assertIn("lib/python3.12/site-packages/pytest", removed)

    def test_a_tree_that_cannot_be_cleaned_is_refused_rather_than_shipped(self):
        packaging = load_packager()
        with tempfile.TemporaryDirectory() as temporary:
            root = self.staged_runtime(Path(temporary))
            with mock.patch.object(packaging, "_remove", lambda path: 0):
                with self.assertRaisesRegex(RuntimeError, "test-only files survive"):
                    packaging.strip_test_only(root)

    def test_the_staging_step_removes_pytest_from_the_runtime_it_copies(self):
        """The function `stage_voice.stage()` calls, on a tree that carries a stray pytest."""
        staging = PROJECT_ROOT / "packaging" / "stage_voice.py"
        spec = importlib.util.spec_from_file_location("stage_voice_under_test", staging)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = self.staged_runtime(Path(temporary))

            stripped = module.strip_runtime_tests(root)

            self.assertIn("lib/python3.12/site-packages/pytest", stripped["removed"])
            self.assertIn("bin/pytest", stripped["removed"])
            self.assertEqual(load_packager().test_only_hits(root), [])

    def test_build_and_staging_both_run_the_sweep_before_the_runtime_is_used(self):
        """The sweep must be wired in, not only available: a helper nobody calls changes nothing."""
        build = (PROJECT_ROOT / "packaging" / "voice-runtime" / "build.sh").read_text(
            encoding="utf-8"
        )
        staging = (PROJECT_ROOT / "packaging" / "stage_voice.py").read_text(encoding="utf-8")

        self.assertLess(
            build.index('runtime_packaging.py" strip-tests "$STAGE"'),
            build.index('runtime_packaging.py" audit "$STAGE"'),
        )
        self.assertLess(
            staging.index("stripped = strip_runtime_tests(resources / 'runtimes/voice')"),
            staging.index("runtime_hash = tree_digest(resources / 'runtimes/voice')"),
        )


class RuntimeNormalizationTests(unittest.TestCase):
    """The recipe step that made a runtime's digest its content instead of its build path."""

    def test_prune_build_junk_removes_markers_and_keeps_code(self):
        packaging = load_packager()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "lib" / "pkg-1.0.dist-info").mkdir(parents=True)
            (root / "lib" / "__pycache__").mkdir()
            (root / "lib" / "used" / "__pycache__").mkdir(parents=True)
            (root / "lib" / "used" / "__pycache__" / "state.json").write_text("{}")
            for name in ("lib/.DS_Store", "lib/pkg-1.0.dist-info/uv_cache.json",
                         "lib/pkg-1.0.dist-info/BUILD", "lib/mod.pyc", "lib/keep.py"):
                (root / name).write_text("x")

            removed = packaging.prune_build_junk(root)

            self.assertTrue((root / "lib" / "keep.py").is_file())
            self.assertIn("lib/__pycache__", removed)
            self.assertIn("lib/mod.pyc", removed)
            for name in ("lib/.DS_Store", "lib/pkg-1.0.dist-info/uv_cache.json",
                         "lib/pkg-1.0.dist-info/BUILD"):
                self.assertIn(name, removed)
                self.assertFalse((root / name).exists())
            self.assertTrue((root / "lib" / "used" / "__pycache__").is_dir(),
                            "a cache directory that still holds something is not junk")

    def test_normalize_records_clears_the_self_entry_and_hashes_content(self):
        packaging = load_packager()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist = root / "lib" / "pkg-1.0.dist-info"
            dist.mkdir(parents=True)
            (root / "lib" / "mod.py").write_text("print('hello')\n")
            record = dist / "RECORD"
            # RECORD paths are relative to the directory that holds the .dist-info, as pip writes
            # them: `lib` here, `site-packages` in a real runtime.
            record.write_text(
                "pkg-1.0.dist-info/RECORD,sha256=stale-of-a-longer-build-path,99\n"
                "mod.py,sha256=stale-of-the-unrewritten-shebang,99\n"
            )

            changed = packaging.normalize_record_files(root)

            rows = record.read_text().splitlines()
            self.assertEqual(rows[0], "pkg-1.0.dist-info/RECORD,,")
            digest = base64.urlsafe_b64encode(
                hashlib.sha256((root / "lib" / "mod.py").read_bytes()).digest()
            ).rstrip(b"=").decode()
            size = (root / "lib" / "mod.py").stat().st_size
            self.assertEqual(rows[1], f"mod.py,sha256={digest},{size}")
            self.assertEqual(changed, ["lib/pkg-1.0.dist-info/RECORD"])
            self.assertEqual(packaging.normalize_record_files(root), [],
                             "a second run changes nothing")

    def test_normalize_records_drops_missing_and_link_entries(self):
        packaging = load_packager()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist = root / "lib" / "pkg-1.0.dist-info"
            dist.mkdir(parents=True)
            (root / "lib" / "real.py").write_text("x\n")
            os.symlink(root / "lib" / "real.py", root / "lib" / "link.py")
            record = dist / "RECORD"
            record.write_text(
                "missing.py,sha256=gone,1\nlink.py,sha256=link,1\nreal.py,sha256=old,1\n"
            )

            packaging.normalize_record_files(root)

            self.assertEqual([row.split(",")[0] for row in record.read_text().splitlines()],
                             ["real.py"])


if __name__ == "__main__":
    unittest.main()
