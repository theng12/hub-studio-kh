"""Run the Voice Studio product suite in a clean child interpreter.

`components/voice/app/tests` is Voice Studio's own product suite — the catalogue,
the generation manager, the Qwen quality gates, the reference-audio contract, the
Hub-facing endpoints. It sits under a component rather than under `tests/`, so the
release gate's `unittest discover -s tests` never saw it, and it had drifted to 40
failures without anyone being told. This file is the bridge: a `TestCase` (so
`unittest discover` collects it) that runs the whole suite in a subprocess and
fails loudly if it fails, if it runs nothing, or if it exceeds its budget.

It deliberately runs the same command a developer runs by hand:

    PYTHONPATH=<repo>/.build/test-deps <repo>/.build/voice-runtime/bin/python3 \
        -m pytest -q components/voice/app/tests

The suite takes about 30 s on this Mac; the 300 s timeout is an order of
magnitude of headroom, not a working limit.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import unittest


class VoiceProductSuiteTests(unittest.TestCase):
    def test_voice_studio_product_suite_in_isolated_subprocess(self) -> None:
        root = Path(__file__).resolve().parents[1]
        runtime = Path(
            os.environ.get("STUDIOHUB_TEST_RUNTIME")
            or root / ".build" / "voice-runtime" / "bin" / "python3"
        )
        interpreter = str(runtime) if runtime.is_file() else sys.executable
        local_deps = root / ".build" / "test-deps"
        shared_deps = root.parent.parent / ".build" / "test-deps"
        deps = local_deps if local_deps.exists() else shared_deps
        log_path = root / ".build" / "voice-product-suite.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        environment = os.environ.copy()
        inherited_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(deps), inherited_pythonpath) if part
        )
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        # The gate's own environment must not reach the child: these variables
        # rebind data directories and packaged-mode behaviour at import.
        for key in (
            "STUDIOHUB_DATA_DIR", "STUDIOHUB_WORKERS_FILE", "STUDIOHUB_PACKAGED",
            "STUDIOHUB_REQUIRE_AUTH",
        ):
            environment.pop(key, None)

        completed = subprocess.run(
            [interpreter, "-m", "pytest", "-q", "components/voice/app/tests"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        output = completed.stdout + completed.stderr
        log_path.write_text(output, encoding="utf-8")
        tail = output.strip().splitlines()[-15:]

        self.assertEqual(
            completed.returncode, 0,
            f"the Voice Studio product suite failed (exit {completed.returncode}); "
            f"see {log_path}\n" + "\n".join(tail),
        )
        # A subprocess that collects nothing exits 5, and a filter typo can make
        # pytest report "no tests ran" while still exiting 0. Either way this must
        # not look like a pass for a suite that is supposed to be wide.
        self.assertNotIn("no tests ran", output, f"pytest collected nothing:\n{output}")
        self.assertIn("passed", output, f"pytest reported no passing tests:\n{output}")
        match = re.search(r"(\d+) passed", output)
        self.assertIsNotNone(match, f"could not read a passed count:\n{output}")
        assert match is not None  # for type checkers; the assertion above is the gate
        self.assertGreater(
            int(match.group(1)), 100,
            f"the voice suite is far smaller than it should be:\n{output}",
        )


if __name__ == "__main__":
    unittest.main()
