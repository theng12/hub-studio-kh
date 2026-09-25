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

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


class VoiceProductSuiteTests(unittest.TestCase):
    @staticmethod
    def _keep_failure(path: Path, output: str, outcome: str) -> None:
        """Write a failing child's output where the next passing run cannot reach it.

        The header is not decoration. This file survives passes by design, so a reader has to be
        able to tell *when* it failed and whether it is the failure they are looking at — a stale
        log presented as a current one would be worse than the missing log it replaced.
        """
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        path.write_text(
            "# The Voice Studio product suite failed on the run recorded here.\n"
            f"# Outcome: {outcome}. Recorded {stamp} (UTC).\n"
            "# Written only on failure, and a later passing run leaves it alone, so this is the\n"
            "# most recent FAILURE rather than the most recent run. The last run of any kind is\n"
            "# beside it in voice-product-suite.log.\n"
            "\n" + output,
            encoding="utf-8",
        )

    def test_the_failure_log_is_written_where_a_pass_cannot_reach_it(self) -> None:
        """The mechanism, on its own, because the gate it protects cannot be made to flake twice.

        What went wrong the first time was not the child failing — that happens — it was the
        evidence being overwritten before it could be read. This pins the two properties that fix
        that: the file is named apart from the ordinary log, and it says what happened and when, so
        a log left over from an earlier failure cannot be mistaken for a current one.
        """
        with tempfile.TemporaryDirectory() as folder:
            failing = Path(folder) / "voice-product-suite.failing.log"
            self.assertFalse(failing.exists())
            self._keep_failure(failing, "child output line\nFAILED tests/whatever\n", "exit 1")
            kept = failing.read_text(encoding="utf-8")
            self.assertIn("child output line", kept, "the child's own output must be kept")
            self.assertIn("exit 1", kept, "and what happened to it")
            self.assertIn("Recorded ", kept, "and when, so a stale log is recognisable")
            self.assertIn("most recent FAILURE", kept,
                          "and that a later pass does not replace it")
            # A second failure replaces it — the newest failure is the interesting one — and the
            # ordinary log is a different file, so writing one cannot destroy the other.
            self._keep_failure(failing, "second failure\n", "exit 2")
            self.assertIn("second failure", failing.read_text(encoding="utf-8"))
            self.assertNotEqual(failing.name, "voice-product-suite.log")

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
        # The failing child's output goes here, and a later *passing* run never overwrites it.
        #
        # The first time this gate failed it left nothing behind: the log was written
        # unconditionally, so the next green run replaced the evidence with its own before anyone
        # could read it. All that survived was "the child exited nonzero", which is not enough to
        # fix anything — a gate whose failures cannot be diagnosed is a gate people learn to
        # re-run, and that is the same as not having one. This file is written only on failure, so
        # it always holds the most recent failure rather than the most recent run.
        failing_path = root / ".build" / "voice-product-suite.failing.log"
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

        try:
            completed = subprocess.run(
                [interpreter, "-m", "pytest", "-q", "components/voice/app/tests"],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=300,
            )
        except subprocess.TimeoutExpired as expired:
            # A hang is a failure too, and it used to leave no log at all: `run` raises before
            # anything is written, so the most interesting case — the suite that never finished —
            # was the one case with no evidence. Whatever the child managed to say is kept.
            partial = (expired.stdout or "") + (expired.stderr or "")
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", "replace")
            self._keep_failure(failing_path, partial, "timed out: killed after 300 s")
            self.fail(f"the Voice Studio product suite did not finish within 300 s; "
                      f"see {failing_path}\n" + "\n".join(partial.strip().splitlines()[-15:]))

        output = completed.stdout + completed.stderr
        log_path.write_text(output, encoding="utf-8")
        tail = output.strip().splitlines()[-15:]
        if completed.returncode != 0:
            self._keep_failure(failing_path, output, f"exit {completed.returncode}")

        self.assertEqual(
            completed.returncode, 0,
            f"the Voice Studio product suite failed (exit {completed.returncode}); "
            f"the child's own output is in {failing_path} and kept there until the next failure\n"
            + "\n".join(tail),
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
