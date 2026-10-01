"""A Hub reports which job each machine is on, so a Gateway can name it instead of guessing.

The contract is ``current_job = {job_id, modality, model, label, started_at}`` on a fleet-activity
machine row, on the node snapshot (``/api/hub/resources``) and on every ``/api/hub/fleet/overview``
row, and ``None`` for a machine that is not working. It carries no prompt text.
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
                      str(WORKTREE / ".build/voice-runtime/bin/python3"),
                      str(WORKTREE.parents[1] / ".build/voice-runtime/bin/python3")):
        if candidate and Path(candidate).is_file():
            return candidate
    return sys.executable


CHILD = r'''
import json
from components.hub.backend import activity, fleet_nodes

now = 10_000.0
registry = [
    {"id": "image@a", "modality": "image", "machine": "node-a"},
    {"id": "voice@a", "modality": "voice", "machine": "node-a"},
    {"id": "image@b", "modality": "image", "machine": "node-b"},
]
statuses = {
    "image@a": {"status": "up", "activity_support": "available", "activity_received_at": now,
                "activity": {"schema": "kh-studio.activity.v1", "studio": "image", "observed_at": now,
                             "active": {"id": "job-9", "state": "running", "model": "flux-2-klein-4b-4bit",
                                        "source": "job", "started_at": now - 40, "progress": 0.4},
                             "latest": None}},
    "voice@a": {"status": "up", "activity_support": "available", "activity_received_at": now,
                "activity": {"schema": "kh-studio.activity.v1", "studio": "voice", "observed_at": now,
                             "active": None, "latest": None}},
    "image@b": {"status": "up", "activity_support": "available", "activity_received_at": now,
                "activity": {"schema": "kh-studio.activity.v1", "studio": "image", "observed_at": now,
                             "active": None, "latest": None}},
}
batches = {"batch-1": {"id": "batch-1", "model": "flux-2-klein-4b-4bit", "label": "storystudio",
                       "items": [{"studio": "image@a", "studio_job_id": "job-9", "state": "running",
                                  "started_at": now - 40}]}}

# The compact view a snapshot and the overview read.
jobs = activity.current_jobs(registry, statuses, batches, now=now)
assert set(jobs) == {"node-a"}, jobs
job = jobs["node-a"]
assert set(job) == {"job_id", "modality", "model", "label", "started_at"}, job
assert job["job_id"] == "job-9" and job["modality"] == "image", job
assert job["label"] == "storystudio" and job["started_at"] == now - 40, job
assert job["model"] == "flux-2-klein-4b-4bit", job

# The fleet-activity row says the same, and an idle machine says None rather than omitting it.
rows = {row["machine"]: row for row in
        activity.fleet_snapshot(registry, statuses, batches, None, now=now)["machines"]}
assert rows["node-a"]["current_job"] == job, rows["node-a"]
assert rows["node-b"]["current_job"] is None, rows["node-b"]

# A node's word is cut to the contract: nothing else a node sends reaches the controller.
cut = fleet_nodes._current_job_of({**job, "prompt": "never carried"})
assert cut == job, cut
assert fleet_nodes._current_job_of(None) is None
assert fleet_nodes._current_job_of({"model": "x"}) is None
print(json.dumps({"ok": True}))
'''


class CurrentJobReportingTests(unittest.TestCase):
    def test_current_job_contract(self) -> None:
        env = os.environ.copy()
        env.update({
            "STUDIOHUB_PROJECT_ROOT": str(WORKTREE),
            "STUDIOHUB_PACKAGED": "1",
            "PYTHONPATH": os.pathsep.join([str(WORKTREE), env.get("PYTHONPATH", "")]).rstrip(os.pathsep),
        })
        with tempfile.TemporaryDirectory(prefix="studiohub-currentjob-") as state_dir:
            env["STUDIOHUB_DATA_DIR"] = state_dir
            done = subprocess.run([_runtime(), "-c", textwrap.dedent(CHILD)], cwd=WORKTREE, env=env,
                                  text=True, capture_output=True, check=False)
        self.assertEqual(done.returncode, 0, f"stdout:\n{done.stdout}\nstderr:\n{done.stderr}")
        self.assertIn('"ok": true', done.stdout)


if __name__ == "__main__":
    unittest.main()
