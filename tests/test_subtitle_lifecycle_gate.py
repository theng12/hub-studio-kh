"""The Subtitle lifecycle tests must run, and must not poison the rest of the suite.

Those ten tests were buried inside a 659-line string literal in tests/test_hub_jobs.py: valid test
source that Python parsed as inert text, executed by nothing. They drive a *packaged* Hub with its own
data directory, worker descriptor and auth requirement, and they set `STUDIOHUB_*` at import — which
rebinds the Hub's data directory for every module imported after them. Run in-process they made six
other tests fail; run in their own process they pass.

So they live in `tests/_subtitle_lifecycle.py` (no `test_` prefix, so discovery skips it) and this
gate runs them as a subprocess. They stay in the suite — which is the point, since being outside it is
how they went unrun in the first place — without touching anyone else's environment.
"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".build/voice-runtime/bin/python3"


class SubtitleLifecycleGate(unittest.TestCase):
    def test_the_recovered_subtitle_lifecycle_tests_pass_in_isolation(self):
        interpreter = str(PYTHON) if PYTHON.is_file() else sys.executable
        completed = subprocess.run(
            [interpreter, "-m", "unittest", "tests._subtitle_lifecycle", "-v"],
            cwd=ROOT, capture_output=True, text=True, timeout=900)
        tail = (completed.stdout + completed.stderr).strip().splitlines()[-12:]
        self.assertEqual(completed.returncode, 0,
                         "the Subtitle lifecycle tests failed:\n" + "\n".join(tail))
        self.assertIn("Ran 10 tests", completed.stderr + completed.stdout,
                      "all ten must run, not fewer: " + "\n".join(tail))


if __name__ == "__main__":
    unittest.main()
