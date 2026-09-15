"""Focused contract tests for the standalone Hub statistics route.

The Hub backend reads its state directory while importing modules.  Run the
fixture in a child process with a temporary state directory so these tests
never touch the checkout's live Hub database or in-memory job registries.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


WORKTREE = Path(__file__).resolve().parents[1]


def _runtime() -> str:
    candidates = [
        os.environ.get("STUDIOHUB_TEST_RUNTIME"),
        str(WORKTREE / ".build/voice-runtime/bin/python3"),
        str(WORKTREE.parents[1] / ".build/voice-runtime/bin/python3"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return sys.executable


CHILD_FIXTURE = r'''
import json
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from components.hub import statistics
from components.hub.backend import activity, broker, ledger, transcription_jobs


def snapshot(studio, *, job_id, state, source, model, operation, observed_at,
             finished_at=None, updated_at=None):
    item = {
        "id": job_id,
        "state": state,
        "model": model,
        "operation": operation,
        "source": source,
        "origin": "local_ui" if source == "direct" else "hub",
        "created_at": observed_at,
    }
    if state == "running":
        item.update({"started_at": observed_at, "updated_at": updated_at or observed_at})
    else:
        item.update({
            "started_at": observed_at - 4,
            "updated_at": updated_at or finished_at,
            "finished_at": finished_at,
            "runtime_s": 4,
        })
    return {"schema": "kh-studio.activity.v1", "studio": studio,
            "observed_at": observed_at,
            "active": item if state == "running" else None,
            "latest": item if state != "running" else None}


now = time.time()
old = now - 3 * 3600
very_old = now - 25 * 3600
registry = [
    {"id": "image-a", "modality": "image", "machine": "node-a"},
    {"id": "voice-b", "modality": "voice", "machine": "node-b"},
    {"id": "subtitle-a", "modality": "transcription", "machine": "node-a"},
]
statuses = {
    "image-a": {
        "status": "up", "activity_support": "available",
        "activity_received_at": old,
        "activity": snapshot(
            "image", job_id="image-old", state="running", source="direct",
            model="image-model", operation="image", observed_at=old,
        ),
    },
    "voice-b": {
        "status": "up", "activity_support": "available",
        "activity_received_at": now - 5,
        "activity": snapshot(
            "voice", job_id="voice-current", state="running", source="direct",
            model="voice-model", operation="speech", observed_at=now - 5,
            updated_at=now - 5,
        ),
    },
    "subtitle-a": {"status": "up", "activity_support": "unavailable"},
}

ledger.record_asset(
    id="image-job", created_at=now - 600, source="job", modality="image",
    studio="image@node-a", machine="node-a", model="image-model",
    artifact_path="/tmp/image-job.png", runtime_s=11,
)
ledger.record_asset(
    id="image-direct", created_at=now - 700, source="direct", modality="image",
    studio="image@node-a", machine="node-a", model="image-model",
    artifact_path="/tmp/image-direct.png", runtime_s=13,
)
# A completed transcription is the one historical Subtitle item.  Its error
# event and an active event represent attempts/states, not additional items.
ledger.record_activity_event(
    machine="node-a", studio="subtitle@node-a", job_id="subtitle-1",
    state="error", model="whisper-small", source="direct",
    operation="transcription", observed_at=now - 550,
    activity_received_at=now - 550, finished_at=now - 550, runtime_s=2,
)
ledger.record_activity_event(
    machine="node-a", studio="subtitle@node-a", job_id="subtitle-1",
    state="done", model="whisper-small", source="direct",
    operation="transcription", observed_at=now - 500,
    activity_received_at=now - 500, finished_at=now - 500, runtime_s=7,
)
ledger.record_activity_event(
    machine="node-a", studio="subtitle@node-a", job_id="subtitle-old",
    state="done", model="whisper-small", source="direct",
    operation="transcription", observed_at=very_old,
    activity_received_at=very_old, finished_at=very_old, runtime_s=7,
)

broker.batches.clear()
broker.batches["voice-batch"] = {
    "model": "voice-model",
    "operation": "speech",
    "items": [{
        "studio": "voice-b", "studio_job_id": "voice-current",
        "state": "running", "started_at": now - 5, "progress": 0.25,
    }],
}
transcription_jobs.batches.clear()
transcription_jobs.batches["subtitle-batch"] = {
    "model": "whisper-small",
    "items": [{"studio": "subtitle-a", "studio_job_id": "subtitle-1", "state": "done"}],
}
activity.observe_poll(registry, statuses, broker.batches, now=now)

monitor = SimpleNamespace(registry=registry, status=statuses)
app = FastAPI()
statistics.register_routes(app, monitor)
with TestClient(app) as client:
    response = client.get("/api/hub/stats?hours=24")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == 3, result
    assert result["by_source"] == {"direct": 2, "job": 1}, result
    assert result["by_modality"]["image"]["count"] == 2, result
    assert result["by_modality"]["transcription"]["count"] == 1, result
    assert result["filters"] == {
        "source": "all", "modality": None, "machine": None, "hours": 24,
    }, result
    assert result["timeline"]["bucket_s"] == 3600, result
    assert sum(sum(values) for values in result["timeline"]["series"].values()) == 3

    job = client.get("/api/hub/stats?hours=24&source=job")
    direct = client.get("/api/hub/stats?hours=24&source=direct")
    subtitle = client.get("/api/hub/stats?hours=24&modality=transcription")
    machine = client.get("/api/hub/stats?hours=24&machine=node-a")
    assert job.status_code == direct.status_code == subtitle.status_code == machine.status_code == 200
    assert job.json()["total"] == 1
    assert direct.json()["total"] == 2
    assert subtitle.json()["total"] == 1
    assert machine.json()["total"] == 3
    assert direct.json()["filters"]["source"] == "direct"
    assert subtitle.json()["filters"]["modality"] == "transcription"
    assert machine.json()["filters"]["machine"] == "node-a"

    hour = client.get("/api/hub/stats?hours=1")
    assert hour.status_code == 200, hour.text
    assert hour.json()["total"] == 3
    assert set(hour.json()["fleet_activity"]) == {"schema", "observed_at", "window", "pulse", "machines"}
    fleet = {row["machine"]: row for row in hour.json()["fleet_activity"]["machines"]}
    # The stale image active observation is rejected, while the newer completed
    # Subtitle item remains the machine's current latest telemetry.
    assert fleet["node-a"]["state"] == "just_finished", fleet
    assert fleet["node-a"]["latest"]["job_id"] == "subtitle-1", fleet
    assert fleet["node-a"]["source"] == "direct", fleet
    assert fleet["node-b"]["state"] == "working", fleet
    assert fleet["node-b"]["source"] == "job", fleet
    assert fleet["node-b"]["origin_device"], fleet

    assert client.get("/api/hub/stats?hours=0").status_code == 422
    assert client.get("/api/hub/stats?source=retry").status_code == 422

print(json.dumps({"total": result["total"], "machines": fleet}, sort_keys=True))
'''


class HubStatisticsTests(unittest.TestCase):
    def test_stats_route_counts_items_and_preserves_fleet_freshness(self) -> None:
        with tempfile.TemporaryDirectory(prefix="studiohub-stats-") as state_dir:
            env = os.environ.copy()
            env["STUDIOHUB_DATA_DIR"] = state_dir
            env["STUDIOHUB_PACKAGED"] = "1"
            env["PYTHONPATH"] = os.pathsep.join(
                [str(WORKTREE), env.get("PYTHONPATH", "")]
            ).rstrip(os.pathsep)
            completed = subprocess.run(
                [_runtime(), "-c", textwrap.dedent(CHILD_FIXTURE)],
                cwd=WORKTREE,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(
            completed.returncode,
            0,
            "isolated statistics fixture failed:\nstdout:\n%s\nstderr:\n%s"
            % (completed.stdout, completed.stderr),
        )
        self.assertIn('"total": 3', completed.stdout)


if __name__ == "__main__":
    unittest.main()
