"""Run the packaged-Hub dispatch proof, including the rollback regression test.

`tests/_hub_dispatch_proof.py` is the `_PROOF` program from test_hub_jobs.py, extracted so a test can
be added beside its fixture harness. It sets STUDIOHUB_* and binds the Hub's data directory at import,
so it runs in its own process - in-process it would rebind the environment for every module loaded
after it. This gate is what keeps it in the suite rather than beside it.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".build/voice-runtime/bin/python3"


class HubDispatchProofGate(unittest.TestCase):
    def test_the_dispatch_proof_and_rollback_test_pass_in_isolation(self):
        interpreter = str(PYTHON) if PYTHON.is_file() else sys.executable
        # Running a file puts its own directory on sys.path, so `components` would not import:
        # the proof needs the repository root, which is what it gets from `PYTHONPATH`.
        environment = {**os.environ, "PYTHONPATH": str(ROOT)}
        completed = subprocess.run([interpreter, "tests/_hub_dispatch_proof.py", "-v"],
                                   cwd=ROOT, env=environment, capture_output=True, text=True,
                                   timeout=900)
        output = completed.stdout + completed.stderr
        tail = output.strip().splitlines()[-12:]
        self.assertEqual(completed.returncode, 0,
                         "the dispatch proof failed:\n" + "\n".join(tail))
        self.assertIn("Ran 11 tests", output,
                      "the proof must run all eleven, including the rollback test: "
                      + "\n".join(tail))


if __name__ == "__main__":
    unittest.main()
