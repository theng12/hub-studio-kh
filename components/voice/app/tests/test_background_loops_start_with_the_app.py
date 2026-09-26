"""The Voice worker's background loops belong to the app lifecycle, not to the import.

Both loops started when `backend.main` was imported, so every process that imported it got
them — a test run included. Two real consequences, and the second is why this file exists:

* the idle-release ticker released whichever managers a test had just injected, so
  `test_memory_policy.py::test_memory_api_frontend_and_process_title` failed intermittently
  with `last_release_details.released is False`. The test's own `POST /api/memory/release`
  found nothing loaded because the 5-second ticker had already unloaded it, and the ticker
  had done it through the test's own fakes and the test's own settings file;
* the retention loop enforces the operator's policy against the real `OUTPUT_DIR` 60 seconds
  after import, so a test session could delete real generated audio in
  `~/Library/Application Support/StudioHub Standalone/voice/output`.

`import-time side effect` and `test-only problem` are different claims. The first loop is
what made a gate flaky; the second could destroy the operator's output. Both have the same
root cause, so both are pinned here.

The Music and Subtitle workers already bind their loops when the app is built, for the same
reason. An import is not a lifecycle; startup is.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from backend import main, memory_policy, storage_policy

REPO_ROOT = Path(__file__).resolve().parents[4]

# Import in a *fresh* interpreter: this session has already imported `backend.main`, so it
# cannot observe what that import does. The child prints its live threads behind a marker
# because the import also writes its own progress lines.
PROBE = textwrap.dedent(
    """
    import threading
    import backend.main  # noqa: F401
    names = sorted(thread.name for thread in threading.enumerate())
    print("THREADS:" + ",".join(names))
    """
)


def test_importing_the_worker_starts_no_background_loops():
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(part for part in sys.path if part)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert completed.returncode == 0, f"importing the worker failed:\n{completed.stderr}"
    markers = [line for line in completed.stdout.splitlines() if line.startswith("THREADS:")]
    assert markers, f"the probe printed no thread list:\n{completed.stdout}"
    live = set(markers[-1].removeprefix("THREADS:").split(","))
    for name in ("memory-policy", "output-storage-cleanup"):
        assert name not in live, (
            f"importing `backend.main` started the {name!r} thread. It acts on the operator's "
            "real data directory, so an import — including a test run — must not start it. "
            "Register it on the app's startup instead (see `_start_background_loops`)."
        )


def test_the_startup_handler_binds_both_loops(tmp_path, monkeypatch):
    """The loops must still start in production, which is the app's startup — not nowhere.

    This asserts the wiring without starting the real threads. Starting them here would leave
    a live ticker running for the rest of the suite and make `test_memory_policy.py` racy
    again, which is the defect this file is about.
    """
    bound: dict[str, tuple] = {}
    monkeypatch.setattr(
        storage_policy, "start_background",
        lambda manager, output_dir: bound.setdefault("storage", (manager, output_dir)),
    )
    monkeypatch.setattr(
        memory_policy, "start_background",
        lambda gen_manager, stt_manager, gpu_lock: bound.setdefault(
            "memory", (gen_manager, stt_manager, gpu_lock)
        ),
    )
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)

    assert main._start_background_loops in main.app.router.on_startup, (
        "`_start_background_loops` is not registered as a startup handler, so neither loop "
        "would ever start: the worker would never release an idle model and never enforce the "
        "operator's retention policy."
    )

    main._start_background_loops()

    assert bound["storage"] == (main.gen_manager, tmp_path), (
        "the retention loop must be bound to the worker's own manager and to the configured "
        "output directory, so it enforces the policy on the operator's real output."
    )
    assert bound["memory"] == (main.gen_manager, main.stt_manager, main._GEN_LOCK), (
        "the idle-release loop must be bound to both managers and the shared accelerator lock."
    )
