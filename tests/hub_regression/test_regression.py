"""Run the adapted upstream Hub core cases in a clean child interpreter."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_adapted_upstream_hub_cases_in_isolated_subprocess() -> None:
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
        timeout=60,
    )
    log_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    assert completed.returncode == 0, (
        f"isolated Hub regression failed (exit {completed.returncode}); see {log_path}\n"
        f"{completed.stdout}\n{completed.stderr}"
    )
