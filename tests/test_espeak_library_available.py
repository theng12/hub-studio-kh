"""Kokoro's phonemizer must find espeak, which this app ships as a library rather than a command.

The runtime bundles `espeakng_loader` - `libespeak-ng.dylib` plus 400 files of `espeak-ng-data` - and
no `espeak-ng` executable. espeak's default is to run a command, so the Kokoro pipeline started and
died with `SystemExit: 1` on every job, while the model's files verified perfectly. Pointing the
phonemizer at the bundled library is what the loader is for.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.voice.app.backend import generation  # noqa: E402


class EspeakLibraryTests(unittest.TestCase):
    def test_the_helper_reports_and_points_at_a_real_library(self):
        self.assertTrue(generation.ensure_espeak_available(),
                        "the bundled espeak library must be found in this environment")
        library = os.environ.get("PHONEMIZER_ESPEAK_LIBRARY")
        data = os.environ.get("ESPEAK_DATA_PATH")
        self.assertTrue(library and Path(library).is_file(), f"library: {library}")
        self.assertTrue(data and Path(data).is_dir(), f"data: {data}")

    def test_an_operators_own_espeak_is_not_overridden(self):
        """`setdefault`, not assignment: someone with their own espeak keeps it."""
        code = (
            "import os, sys; sys.path.insert(0, %r);"
            "os.environ['PHONEMIZER_ESPEAK_LIBRARY'] = '/operator/choice.dylib';"
            "from components.voice.app.backend import generation;"
            "print(os.environ['PHONEMIZER_ESPEAK_LIBRARY'])" % str(ROOT))
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                   timeout=300, cwd=ROOT)
        self.assertIn("/operator/choice.dylib", completed.stdout,
                      completed.stdout + completed.stderr)

    def test_it_is_wired_at_import_so_no_job_has_to_remember(self):
        self.assertTrue(generation.ENSURE_ESPEAK_AT_IMPORT)

    def test_the_vendored_command_is_on_path_and_runs(self):
        """The phonemizer runs a command; this app ships it, so the tools directory must be on PATH.

        Vendored because a Mac that has never heard of Homebrew must still be able to phonemize:
        the binary plus its two libraries live in the runtime's bin directory, the libraries are
        referenced through @loader_path, and both are ad-hoc signed so macOS will execute them.
        """
        import shutil
        import subprocess
        # What matters is that the phonemizer's lookup succeeds, not which directory provided it:
        # `paths.tools_dir()` is the bundle's tools folder, which exists only once the app is staged,
        # so in a checkout the command is found beside the interpreter that is running.
        found = shutil.which("espeak-ng")
        self.assertIsNotNone(found, "espeak-ng must be findable on PATH: " + os.environ.get("PATH", ""))
        command = Path(found)
        completed = subprocess.run([str(command), "-q", "--ipa", "hello"], capture_output=True,
                                   text=True, timeout=60,
                                   env={"PATH": "/usr/bin:/bin",
                                        "ESPEAK_DATA_PATH": os.environ.get("ESPEAK_DATA_PATH", "")})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("h", completed.stdout.strip(), completed.stdout)


if __name__ == "__main__":
    unittest.main()
