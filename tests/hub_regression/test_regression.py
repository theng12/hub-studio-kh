"""Run the adapted upstream Hub core cases in a clean child interpreter.

This file is collected by the release gate's `unittest discover -s tests -p 'test_*.py'`, so it has to
be a `TestCase`. It was a bare `test_*` function, which unittest does not collect: the whole
`tests/hub_regression/cases` suite - 54 cases covering dispatch fencing, execution identity, memory
admission, model-failure protection and terminal progress - ran only when somebody remembered to
invoke pytest by hand. It now runs in the gate, where evidence is supposed to come from.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest


class HubRegressionTests(unittest.TestCase):
    def test_adapted_upstream_hub_cases_in_isolated_subprocess(self) -> None:
        root = Path(__file__).resolve().parents[2]
        runtime = Path(os.environ.get("STUDIOHUB_TEST_RUNTIME") or sys.executable)
        local_deps = root / ".build" / "test-deps"
        shared_deps = root.parent.parent / ".build" / "test-deps"
        deps = local_deps if local_deps.exists() else shared_deps
        log_path = root / ".build" / "st02-hub-regression.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        inherited_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(deps), inherited_pythonpath) if part
        )
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        for key in (
            "STUDIOHUB_DATA_DIR", "STUDIOHUB_WORKERS_FILE", "STUDIOHUB_PACKAGED",
            "STUDIOHUB_REQUIRE_AUTH",
        ):
            environment.pop(key, None)

        completed = subprocess.run(
            [str(runtime), "-m", "pytest", "-q", "tests/hub_regression/cases"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        log_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")
        self.assertEqual(
            completed.returncode, 0,
            f"isolated Hub regression failed (exit {completed.returncode}); see {log_path}\n"
            f"{completed.stdout}\n{completed.stderr}",
        )
        # A subprocess that collects nothing exits 5 (or 0 with "no tests ran") and would otherwise
        # look like a pass for the single most stateful suite in the project.
        self.assertIn("passed", completed.stdout, f"pytest collected nothing: {completed.stdout}")


if __name__ == "__main__":
    unittest.main()
