"""A failure inside the generation child must say *what* failed, not just that something did.

Generation runs in a spawned process, and that process reported failures as `SystemExit: 1` with no
traceback. So the parent, the job record, the log and the operator all saw the same six characters
while the cause - a missing model, a bad path, a library calling `sys.exit` - stayed inside the child.
That blindness is what made a Kokoro failure take four separate diagnoses: every one had to be found
from outside the process that knew the answer.
"""
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.voice.app.backend import native_executor  # noqa: E402


class _Custom(RuntimeError):
    """A distinctive exception type, so the test can assert the type survives the process boundary."""


def _fail(payload, cancel):
    """Module level on purpose: the child is a `spawn` process, so the callable must be picklable."""
    kind = payload.get("kind")
    if kind == "exit":
        raise SystemExit(1)
    if kind == "custom":
        raise _Custom("the real reason")
    raise RuntimeError("x" * 100)


class NativeExecutorReportingTests(unittest.TestCase):
    def _run(self, kind):
        executor = native_executor.ExclusiveNativeExecutor(_fail)
        try:
            return executor.run({"kind": kind}, threading.Event())
        finally:
            executor.close()

    def test_a_raising_native_call_reports_its_traceback(self):
        outcome = self._run("exit")
        error = str(getattr(outcome, "error", "") or "")
        self.assertIn("SystemExit", error)
        self.assertIn("Traceback", error, f"the child's traceback must survive: {error[:300]}")
        self.assertIn("_fail", error, "and name the frame that raised")

    def test_the_original_exception_type_is_visible(self):
        outcome = self._run("custom")
        error = str(getattr(outcome, "error", "") or "")
        self.assertIn("_Custom", error)
        self.assertIn("the real reason", error)

    def test_the_traceback_is_bounded(self):
        """A deep or repetitive failure must not put an unbounded string in the job record."""
        outcome = self._run("long")
        self.assertLessEqual(len(str(getattr(outcome, "error", "") or "")), 8000)


if __name__ == "__main__":
    unittest.main()
