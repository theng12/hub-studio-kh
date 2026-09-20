"""Kokoro's phonemizer must find espeak, which this app ships as a library rather than a command.

The pinned Kokoro/Misaki fallback uses the shared library provided by `espeakng_loader`.
Verify that real path with no host executable or model weights.
"""
import os
import subprocess
import sys
import tempfile
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

    def test_production_espeak_fallback_works_without_a_host_command(self):
        """Kokoro's real fallback must use the bundled library, not an espeak executable."""
        code = """
import shutil
import sys
sys.path.insert(0, {root!r})
assert sys.path[0] == {root!r}
assert shutil.which("espeak-ng") is None, shutil.which("espeak-ng")
from components.voice.app.backend import generation
from misaki.espeak import EspeakFallback

class Token:
    text = "hello"

phonemes, _ = EspeakFallback(british=False)(Token())
assert phonemes.strip(), phonemes
print("phonemes:" + phonemes)
""".format(root=str(ROOT))
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            environment = os.environ.copy()
            for name in ("PHONEMIZER_ESPEAK_LIBRARY", "PHONEMIZER_ESPEAK_PATH",
                         "ESPEAK_DATA_PATH"):
                environment.pop(name, None)
            environment.update({
                "PATH": "/usr/bin:/bin",
                "HOME": temporary,
                "TMPDIR": temporary,
                "XDG_CACHE_HOME": str(cache),
                "HF_HOME": str(cache / "huggingface"),
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_DATASETS_OFFLINE": "1",
                "TORCH_HOME": str(cache / "torch"),
                "MLX_DATA_DIR": str(cache / "mlx"),
                "STUDIOHUB_DATA_DIR": str(Path(temporary) / "state"),
                "STUDIOHUB_MODELS_DIR": str(Path(temporary) / "models"),
            })
            completed = subprocess.run(
                [sys.executable, "-I", "-c", code],
                capture_output=True, text=True, timeout=300,
                cwd=ROOT, env=environment,
            )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertRegex(completed.stdout, r"phonemes:\S", completed.stdout)


if __name__ == "__main__":
    unittest.main()
