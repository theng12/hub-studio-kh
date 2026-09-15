"""Isolated tests for the packaged Hub's scheduler pause boundary."""

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
    candidates = [
        os.environ.get("STUDIOHUB_TEST_RUNTIME"),
        str(WORKTREE / ".build/voice-runtime/bin/python3"),
        str(WORKTREE.parents[1] / ".build/voice-runtime/bin/python3"),
    ]
    return next((candidate for candidate in candidates
                 if candidate and Path(candidate).is_file()), sys.executable)


PROOF = r'''
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from components.hub import operator_controls
from components.hub.backend import broker, registry, transcription_jobs


workers = [
    {"id": "image", "modality": "image", "machine": "node-a",
     "host": "127.0.0.1", "port": 51001, "local": True},
    {"id": "subtitle", "modality": "transcription", "machine": "node-a",
     "host": "127.0.0.1", "port": 51002, "local": True},
]
monitor = SimpleNamespace(
    registry=workers,
    status={worker["id"]: {"status": "up"} for worker in workers},
)
broker.configure_monitor(monitor)
broker._busy.clear()
broker._maintenance.clear()
broker._machine_protection.clear()
broker._external_machine_leases.clear()
broker.wake_dispatcher = Mock(wraps=broker.wake_dispatcher)
registry._flags_cache = None
transcription_jobs._wakeup.clear()

active_item = {"index": 0, "state": "running", "studio": "image"}
broker.batches.clear()
broker.batches["active-batch"] = {"id": "active-batch", "items": [active_item]}
app = FastAPI()
operator_controls.register_routes(app, monitor)

async def subtitle_eligibility():
    original = monitor.scheduling_transcription if hasattr(monitor, "scheduling_transcription") else None
    async def availability(_studio):
        return {"available": True, "models": [{"repo": "whisper-small", "cached": True}]}
    monitor.scheduling_transcription = availability
    try:
        return await transcription_jobs._eligible_studios(monitor, "whisper-small")
    finally:
        if original is None:
            del monitor.scheduling_transcription
        else:
            monitor.scheduling_transcription = original

with TestClient(app) as client:
    path_machine = "/api/hub/registry/machines/node-a/enabled"
    path_studio = "/api/hub/registry/studios/subtitle/enabled"

    assert client.post(path_machine, json={"enabled": False}).json() == {
        "ok": True, "machine": "node-a", "enabled": False,
    }
    assert registry.machine_enabled("node-a") is False
    assert json.loads(registry.FLAGS_FILE.read_text())["node-a"]["enabled"] is False
    assert broker._eligible_studios("image", "local") == []
    assert asyncio.run(subtitle_eligibility()) == []
    assert active_item == {"index": 0, "state": "running", "studio": "image"}

    assert client.post(path_machine, json={"enabled": True}).status_code == 200
    assert registry.machine_enabled("node-a") is True
    assert broker._eligible_studios("image", "local")[0]["id"] == "image"

    assert client.post(path_studio, json={"enabled": False}).json() == {
        "ok": True, "studio": "subtitle", "machine": "node-a", "enabled": False,
    }
    assert registry.studio_enabled("node-a", "subtitle") is False
    assert json.loads(registry.FLAGS_FILE.read_text())["node-a"]["studios"]["subtitle"]["enabled"] is False
    assert asyncio.run(subtitle_eligibility()) == []
    assert broker._eligible_studios("image", "local")[0]["id"] == "image"

    assert client.post(path_studio, json={"enabled": True}).status_code == 200
    assert registry.studio_enabled("node-a", "subtitle") is True
    assert asyncio.run(subtitle_eligibility())[0]["id"] == "subtitle"
    assert broker.wake_dispatcher.call_count >= 4

    assert client.post(path_machine, json={}).status_code == 400
    assert client.post(path_machine, json={"enabled": 1}).status_code == 400
    assert client.post("/api/hub/registry/machines/unknown/enabled",
                       json={"enabled": True}).status_code == 404
    assert client.post("/api/hub/registry/studios/missing/enabled",
                       json={"enabled": True}).status_code == 404

print("operator controls: flags persisted, broker/subtitle eligibility toggled, active item preserved")
'''


class HubOperatorControlsTests(unittest.TestCase):
    def test_pause_resume_is_persistent_scheduler_only_control(self) -> None:
        with tempfile.TemporaryDirectory(prefix="studiohub-controls-") as state_dir:
            env = os.environ.copy()
            env["STUDIOHUB_DATA_DIR"] = state_dir
            env["STUDIOHUB_PACKAGED"] = "1"
            env["PYTHONPATH"] = os.pathsep.join(
                [str(WORKTREE), env.get("PYTHONPATH", "")]
            ).rstrip(os.pathsep)
            completed = subprocess.run(
                [_runtime(), "-c", textwrap.dedent(PROOF)],
                cwd=WORKTREE,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(
            completed.returncode,
            0,
            "isolated operator-controls fixture failed:\nstdout:\n%s\nstderr:\n%s"
            % (completed.stdout, completed.stderr),
        )
        self.assertIn("active item preserved", completed.stdout)


if __name__ == "__main__":
    unittest.main()
