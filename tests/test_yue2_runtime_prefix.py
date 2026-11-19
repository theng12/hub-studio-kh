"""The shipped YuE2 runtime names nobody: no build user's home path in the generated sysconfig.

Owner-visible consequence: the DMG is a public download, and a scan of the published artifact found
`/Users/<builder>` 21 times in `_sysconfigdata__darwin_darwin.py`. A runtime built on another Mac
shipped somebody else's home directory.
"""
import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "packaging/yue2-runtime/neutralize_prefix.py"
_spec = importlib.util.spec_from_file_location("neutralize_prefix", SCRIPT)
neutralize_prefix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(neutralize_prefix)

GENERATED = "lib/python3.12/_sysconfigdata__darwin_darwin.py"
BUILT_AT = "/Users/somebuilder/.local/share/uv/python/cpython-3.12.14-macos-aarch64-none"


def tree(body: str) -> Path:
    root = Path(tempfile.mkdtemp())
    path = root / GENERATED
    path.parent.mkdir(parents=True)
    path.write_text(body)
    path.chmod(0o444)
    return root


class NeutralizePrefix(unittest.TestCase):
    def test_every_home_path_becomes_the_neutral_root(self) -> None:
        root = tree(f'build_time_vars = {{"prefix": "{BUILT_AT}", "BINDIR": "{BUILT_AT}/bin",\n'
                    f'     "DESTDIRS": "{BUILT_AT} /other"}}\n')
        try:
            self.assertEqual(neutralize_prefix.neutralize(root), ["/Users/somebuilder"])
            text = (root / GENERATED).read_text()
            self.assertNotIn("/Users/", text)
            self.assertEqual(text.count(neutralize_prefix.NEUTRAL), 3)
            self.assertIn(" /other", text)           # a path that is not the prefix is untouched
            self.assertEqual((root / GENERATED).stat().st_mode & 0o777, 0o444)
        finally:
            shutil.rmtree(root)

    def test_a_runtime_with_no_home_path_is_left_byte_identical(self) -> None:
        body = 'build_time_vars = {"prefix": "/opt/x"}\n'
        root = tree(body)
        try:
            self.assertEqual(neutralize_prefix.neutralize(root), [])
            self.assertEqual((root / GENERATED).read_text(), body)
        finally:
            shutil.rmtree(root)

    def test_a_runtime_with_no_recorded_prefix_is_refused_not_guessed(self) -> None:
        root = tree('build_time_vars = {"BINDIR": "/Users/somebuilder/bin"}\n')
        try:
            with self.assertRaises(SystemExit):
                neutralize_prefix.neutralize(root)
        finally:
            shutil.rmtree(root)

    def test_a_missing_file_is_not_an_error(self) -> None:
        root = Path(tempfile.mkdtemp())
        try:
            self.assertEqual(neutralize_prefix.neutralize(root), [])
        finally:
            shutil.rmtree(root)

    def test_the_runtime_still_starts_and_reports_its_own_prefix_after_neutralising(self) -> None:
        """sys.prefix comes from the interpreter's location, not from the recorded build prefix."""
        runtime = Path("/Applications/Hub Studio KH.app/Contents/Resources/runtimes/music-yue2")
        if not runtime.is_dir():
            self.skipTest("the YuE2 runtime is not installed on this Mac")
        work = Path(tempfile.mkdtemp()) / "music-yue2"
        try:
            # APFS clone: read-only content, no real copy cost.
            subprocess.run(["cp", "-cR", str(runtime), str(work)], check=True, timeout=300)
            homes = neutralize_prefix.neutralize(work)
            binaries = neutralize_prefix.neutralize_binaries(work)
            # The builder's own home must be gone from every file — including binaries. Other
            # `/Users/...` strings belong to third-party wheels (a CI runner, a package author)
            # and are out of scope: this release's bar is the build user, not every stranger.
            self.assertTrue(homes, "the installed runtime should still carry the build home")
            for home in set(homes):
                found = subprocess.run(["grep", "-ral", home, str(work)],
                                       capture_output=True, text=True)
                self.assertEqual(found.stdout.split(), [], home)
            # The library's own id is the one Mach-O carrier, and it must read the neutral root.
            self.assertEqual(len(binaries), 1, binaries)
            ident = subprocess.run(["otool", "-D", str(work / "lib/libpython3.12.dylib")],
                                   capture_output=True, text=True).stdout.splitlines()[-1]
            self.assertEqual(ident, neutralize_prefix.NEUTRAL + "/lib/libpython3.12.dylib")
            out = subprocess.run(
                [str(work / "bin/python3"), "-I", "-c",
                 "import sysconfig, sys; print(sysconfig.get_config_var('prefix')); print(sys.prefix)"],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr)
            recorded, actual = out.stdout.split()
            self.assertEqual(recorded, neutralize_prefix.NEUTRAL)
            self.assertEqual(Path(actual).resolve(), work.resolve())
            out = subprocess.run([str(work / "bin/python3"), "-I", "-B", "-c",
                                  "import mlx.core, mlx_lm; print('ok')"],
                                 capture_output=True, text=True, timeout=300)
            self.assertEqual(out.returncode, 0, out.stderr)
        finally:
            # The copied tree is read-only, as the built one is; the test's own copy is disposable.
            subprocess.run(["chmod", "-R", "u+w", str(work.parent)], check=False)
            shutil.rmtree(work.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
