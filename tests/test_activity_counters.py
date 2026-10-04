"""The per-Mac done/failed counters the Gateway's Fleet -> Machines tab draws.

Two rules (2026-10-04): a refusal (`INSUFFICIENT_MEMORY`, the job never reached the engine and ran
on another Mac) is not counted as that Mac's failure, and `/api/hub/stats?activity_since=<epoch>`
counts only from the Gateway's "Reset counters" baseline while the ledger itself keeps every row.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[1]


def _runtime() -> str:
    for candidate in (os.environ.get("STUDIOHUB_TEST_RUNTIME"),
                      str(WORKTREE / ".build/hub-gate/bin/python3"),
                      str(WORKTREE.parents[1] / "studio-hub-kh/.build/hub-gate/bin/python3"),
                      str(WORKTREE.parents[1] / ".build/hub-gate/bin/python3")):
        if candidate and Path(candidate).is_file():
            return candidate
    return sys.executable


CHILD = r'''
import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from components.hub import statistics
from components.hub.backend import activity, ledger

now = 100_000.0
registry = [{"id": "voice@a", "modality": "voice", "machine": "node-a"}]
statuses = {"voice@a": {"status": "up", "activity_support": "available", "activity_received_at": now,
                        "activity": {"schema": "kh-studio.activity.v1", "studio": "voice",
                                     "observed_at": now, "active": None, "latest": None}}}

def record(job_id, state, finished, code=None):
    ledger.record_activity_event(
        machine="node-a", studio="voice@a", job_id=job_id, state=state, model="m", source="job",
        operation="speech", started_at=finished - 10, finished_at=finished, runtime_s=10.0,
        error_code=code, observed_at=finished,
    )

record("old-done", "done", now - 5000)
record("old-fail", "error", now - 4000, "TTS_SECTION_SILENT")
record("refused-1", "error", now - 3000, "INSUFFICIENT_MEMORY")
record("refused-2", "error", now - 60, "INSUFFICIENT_MEMORY")
record("new-done", "done", now - 2000)
record("new-fail", "error", now - 1000, None)

row = activity.fleet_snapshot(registry, statuses, {}, None, now=now)["machines"][0]
# Refusals are not failures, and a refusal as the newest job does not make the Mac need attention.
assert (row["completed"], row["failed"]) == (2, 2), row
assert row["state"] != "needs_attention", row

# The baseline counts only what finished after it; the ledger still holds every row.
row = activity.fleet_snapshot(registry, statuses, {}, now - 2500, now=now)["machines"][0]
assert (row["completed"], row["failed"]) == (1, 1), row
assert len(ledger.activity_events(machine="node-a")) == 6

class Monitor:
    pass
monitor = Monitor()
monitor.registry, monitor.status = registry, statuses
app = FastAPI()
statistics.register_routes(app, monitor)
client = TestClient(app)
import time
baseline = time.time() - 600  # the route reads the real clock
fleet = client.get("/api/hub/stats", params={"activity_since": baseline}).json()["fleet_activity"]
assert fleet["window"]["since_s"] == baseline, fleet["window"]
# A caller that sends no baseline still counts the whole retention window.
assert client.get("/api/hub/stats").json()["fleet_activity"]["window"]["since_s"] < baseline - 86400
assert client.get("/api/hub/stats", params={"activity_since": -1}).status_code == 422
print(json.dumps({"ok": True}))
'''


class MachineCounterTests(unittest.TestCase):
    def test_refusals_are_not_failures_and_the_baseline_starts_the_count(self) -> None:
        with tempfile.TemporaryDirectory(prefix="studiohub-counters-") as state_dir:
            env = os.environ.copy()
            env["STUDIOHUB_DATA_DIR"] = state_dir
            env["STUDIOHUB_PACKAGED"] = "1"
            env["PYTHONPATH"] = os.pathsep.join(
                [str(WORKTREE), env.get("PYTHONPATH", "")]
            ).rstrip(os.pathsep)
            completed = subprocess.run(
                [_runtime(), "-c", textwrap.dedent(CHILD)],
                cwd=WORKTREE, env=env, text=True, capture_output=True, check=False,
            )
        self.assertEqual(
            completed.returncode, 0,
            "isolated counter fixture failed:\nstdout:\n%s\nstderr:\n%s"
            % (completed.stdout, completed.stderr),
        )
        self.assertIn('"ok": true', completed.stdout)


if __name__ == "__main__":
    unittest.main()
