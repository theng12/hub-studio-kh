"""A Music job must be filed as Music, in the live view and in the durable ledger.

``ledger._VALID_OPERATIONS`` held ``{"image", "speech", "transcription"}`` while the live
activity view's copy held ``music`` too. Both the write path and the read path coerced
anything outside the ledger's copy to its default, which is ``"speech"`` for every studio that
is not Image — so every Music activity row was stored **and read back** as ``speech``, and
Music's per-operation history and statistics were filed under Voice. The two views of the same
job disagreed.

The rule asserted here is not membership of a list: it is that the operation the producer
reports is the operation that is stored and read back, that a studio nobody has classified
files under its own name instead of under Voice, and that a nonsense value cannot be persisted
as free text.
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
if str(WORKTREE) not in sys.path:
    sys.path.insert(0, str(WORKTREE))


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
"""One Music job through the real observer cycle, and the same job read back."""
import json
import time

from components.hub.backend import activity, broker, ledger

now = time.time()
registry = [
    {"id": "music", "modality": "music", "machine": "node-a"},
    {"id": "voice", "modality": "voice", "machine": "node-a"},
    {"id": "subtitle", "modality": "transcription", "machine": "node-a"},
]


def snapshot(studio, *, job_id, operation, state, observed_at):
    item = {
        "id": job_id, "state": state, "model": "ace-step-1-5-turbo",
        "operation": operation, "source": "direct", "origin": "local_ui",
        "created_at": observed_at - 4, "started_at": observed_at - 4,
        "updated_at": observed_at,
    }
    if state in ("done", "error", "cancelled"):
        item.update({"finished_at": observed_at, "runtime_s": 4})
    return {"schema": "kh-studio.activity.v1", "studio": studio,
            "observed_at": observed_at,
            "active": item if state == "running" else None,
            "latest": item if state != "running" else None}


statuses = {
    "music": {
        "status": "up", "activity_support": "available", "activity_received_at": now - 2,
        "activity": snapshot("music", job_id="music-1", operation="music",
                             state="running", observed_at=now - 2),
    },
    "voice": {
        "status": "up", "activity_support": "available", "activity_received_at": now - 2,
        "activity": snapshot("voice", job_id="voice-1", operation="speech",
                             state="running", observed_at=now - 2),
    },
    "subtitle": {"status": "up", "activity_support": "unavailable"},
}

# A Hub-owned Music batch, the shape `broker.batches` really holds: no `operation` key.
broker.batches.clear()
broker.batches["music-batch"] = {
    "model": "ace-step-1-5-turbo",
    "items": [{
        "studio": "music", "studio_job_id": "music-owned-1",
        "state": "running", "started_at": now - 3, "progress": 0.5,
    }],
}

activity.observe_poll(registry, statuses, broker.batches, now=now)

live = activity._observed_jobs(registry, statuses, broker.batches, now)
live_by_id = {row["id"]: row for row in live}
stored = {row["job_id"]: row for row in ledger.activity_events(machine="node-a")}

assert "music-1" in live_by_id, sorted(live_by_id)
assert "music-1" in stored, sorted(stored)
assert live_by_id["music-1"]["operation"] == "music", live_by_id["music-1"]
assert stored["music-1"]["operation"] == "music", stored["music-1"]

assert "music-owned-1" in live_by_id, sorted(live_by_id)
assert live_by_id["music-owned-1"]["operation"] == "music", live_by_id["music-owned-1"]
assert stored["music-owned-1"]["operation"] == "music", stored["music-owned-1"]

# Voice keeps the operation it already had, and the two views agree for every row.
assert stored["voice-1"]["operation"] == "speech", stored["voice-1"]
for job_id, row in live_by_id.items():
    if job_id in stored:
        assert stored[job_id]["operation"] == row["operation"], (job_id, row, stored[job_id])

print(json.dumps({"operations": {job_id: row["operation"] for job_id, row in stored.items()}},
                 sort_keys=True))
'''


class MusicOperationIsNotSpeechTests(unittest.TestCase):
    def test_a_music_job_is_stored_and_read_back_as_music(self) -> None:
        with tempfile.TemporaryDirectory(prefix="studiohub-activity-") as state_dir:
            env = os.environ.copy()
            env["STUDIOHUB_DATA_DIR"] = state_dir
            env["STUDIOHUB_PACKAGED"] = "1"
            env["PYTHONPATH"] = os.pathsep.join(
                [str(WORKTREE), env.get("PYTHONPATH", "")]
            ).rstrip(os.pathsep)
            completed = subprocess.run(
                [_runtime(), "-c", textwrap.dedent(CHILD_FIXTURE)],
                cwd=WORKTREE, env=env, text=True, capture_output=True, check=False,
            )
        self.assertEqual(
            completed.returncode, 0,
            "isolated activity fixture failed:\nstdout:\n%s\nstderr:\n%s"
            % (completed.stdout, completed.stderr),
        )
        self.assertIn('"music-1": "music"', completed.stdout)


class OperationVocabularyTests(unittest.TestCase):
    """The rule itself, close to the code that stores it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="studiohub-ledger-")
        from components.hub.backend import ledger
        cls.ledger = ledger
        cls.original_db = ledger.DB_FILE
        ledger.DB_FILE = Path(cls.temporary.name) / "hub.db"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.ledger.DB_FILE = cls.original_db
        cls.temporary.cleanup()

    def _round_trip(self, studio: str, operation) -> str:
        job_id = f"job-{studio}-{operation}"
        self.ledger.record_activity_event(
            machine="node-a", studio=studio, job_id=job_id, state="done",
            model="model", source="direct", operation=operation, observed_at=1.0,
        )
        rows = {row["job_id"]: row
                for row in self.ledger.activity_events(machine="node-a")}
        return rows[job_id]["operation"]

    def test_every_operation_a_studio_reports_survives_the_round_trip(self) -> None:
        from components.hub.backend import vocabulary
        for studio, operation in sorted(
                (studio, operation)
                for studio in vocabulary.WORKER_FOR_MODALITY
                for operation in (studio, "speech")
        ):
            with self.subTest(studio=studio, operation=operation):
                self.assertEqual(self._round_trip(studio, operation), operation)

    def test_a_studio_with_no_operation_files_under_itself_not_under_voice(self) -> None:
        """The closed-list-plus-"speech" default is what hid Music's history under Voice."""
        self.assertEqual(self._round_trip("music", None), "music")
        self.assertEqual(self._round_trip("chat", None), "chat")
        self.assertEqual(self._round_trip("transcription", None), "transcription")
        self.assertEqual(self._round_trip("voice", None), "speech")
        self.assertEqual(self._round_trip("image", None), "image")
        self.assertEqual(self._round_trip("a-new-studio", None), "a-new-studio")

    def test_a_nonsense_operation_is_not_stored_as_free_text(self) -> None:
        self.assertEqual(self._round_trip("music", "not an operation <script>"), "music")
        self.assertEqual(self._round_trip("voice", "x" * 400), "speech")


CHILD_LIVE_QUEUES = r'''
"""Every Hub-owned queue reaches the live activity view, and none of them is restated."""
import ast
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from components.hub import statistics
from components.hub.backend import activity, chat_jobs, ledger

ROOT = Path(os.environ["STUDIOHUB_PROJECT_ROOT"])
BACKEND = ROOT / "components" / "hub" / "backend"


def queue_modules() -> list[str]:
    """Backend modules that own a module-level `batches` queue, found without importing them."""
    found = []
    for path in sorted(BACKEND.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            targets = node.targets if isinstance(node, ast.Assign) else (
                [node.target] if isinstance(node, ast.AnnAssign) else [])
            if any(isinstance(target, ast.Name) and target.id == "batches"
                   for target in targets):
                found.append(path.stem)
                break
    return found


names = queue_modules()
assert names, "no Hub-owned queue modules were found at all"

# Every queue module must publish its own activity view, and the aggregate must include it.
for name in names:
    module = importlib.import_module(f"components.hub.backend.{name}")
    assert callable(getattr(module, "activity_batches", None)), (
        f"{name} owns a queue but publishes no activity_batches(); its jobs would be invisible "
        f"in the polled activity ledger and in /api/hub/stats")
    module.batches.clear()
    module.batches["sentinel"] = {"model": "sentinel-model"}

merged = activity.live_batches()
for name in names:
    assert any(key.endswith("sentinel") for key in merged), (
        f"{name}'s queue is not part of live_batches(): {sorted(merged)}")

# A Chat pack is a job on a machine: the activity view must be able to name it, and the
# statistics route must show that machine as working.
for name in names:
    importlib.import_module(f"components.hub.backend.{name}").batches.clear()
chat_jobs.batches["chat-1"] = {
    "id": "chat-1", "model": "mlx-community/Qwen3.5-9B-4bit", "kind": "scenes",
    "created_at": 1.0, "packs": [{
        "pack_id": "pack-0", "state": "running", "studio": "chat",
        "started_at": 1.0, "scene_ids": ["scene-1"], "messages": [], "params": {},
    }],
}
registry = [{"id": "chat", "modality": "chat", "machine": "node-chat"}]
statuses = {"chat": {"status": "up", "activity_support": "unavailable"}}
rows = activity._broker_jobs(registry, activity.live_batches())
flat = [row for rows_ in rows.values() for row in rows_]
assert any(row["studio"] == "chat" and row["operation"] == "chat" for row in flat), flat

now = 10_000.0
snapshot = activity.fleet_snapshot(registry, statuses, activity.live_batches(), None, now=now)
machines = {row["machine"]: row for row in snapshot["machines"]}
assert "node-chat" in machines, sorted(machines)
assert machines["node-chat"]["state"] == "working", machines["node-chat"]

app = FastAPI()
statistics.register_routes(app, SimpleNamespace(registry=registry, status=statuses))
with TestClient(app) as client:
    response = client.get("/api/hub/stats?hours=24")
    assert response.status_code == 200, response.text
    fleet = {row["machine"]: row for row in response.json()["fleet_activity"]["machines"]}
assert fleet.get("node-chat", {}).get("state") == "working", fleet

stored = [row for row in ledger.activity_events(machine="node-chat")]
assert not stored, "this fixture observes no polls, so nothing should be persisted yet"
print(json.dumps({"queues": names, "chat_state": machines["node-chat"]["state"]}, sort_keys=True))
'''


class EveryQueueReachesLiveActivityTests(unittest.TestCase):
    def test_every_hub_owned_queue_reaches_the_activity_view(self) -> None:
        """A Chat job used to appear in no activity view anywhere.

        The queue set was restated in `monitor.poll_all` and in the statistics route, and both
        copies named the broker and transcription queues only. This walks the backend's own
        queue modules, so the next `*_jobs.py` module is covered the moment it exists.
        """
        env = os.environ.copy()
        env.update({
            "STUDIOHUB_PROJECT_ROOT": str(WORKTREE),
            "STUDIOHUB_PACKAGED": "1",
            "PYTHONPATH": os.pathsep.join(
                [str(WORKTREE), env.get("PYTHONPATH", "")]
            ).rstrip(os.pathsep),
        })
        with tempfile.TemporaryDirectory(prefix="studiohub-queues-") as state_dir:
            env["STUDIOHUB_DATA_DIR"] = state_dir
            completed = subprocess.run(
                [_runtime(), "-c", textwrap.dedent(CHILD_LIVE_QUEUES)],
                cwd=WORKTREE, env=env, text=True, capture_output=True, check=False,
            )
        self.assertEqual(
            completed.returncode, 0,
            "isolated live-activity fixture failed:\nstdout:\n%s\nstderr:\n%s"
            % (completed.stdout, completed.stderr),
        )
        self.assertIn('"chat_state": "working"', completed.stdout)


if __name__ == "__main__":
    unittest.main()
