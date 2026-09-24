"""One bad manifest must not blank every model picker on every Mac.

`GET /api/hub/model-packages` built its rows with a single comprehension and answered 503 for
the whole catalog when any one manifest raised. The native app reads that route with a hard
``try`` inside its refresh, so one malformed manifest did not degrade one list — it marked the
whole Hub unreachable and left every model picker empty. That is the 0.6.57 failure mode, and
the amplifier was still live after the engine map itself was fixed.

The route is now built one manifest at a time, and a manifest that cannot be used produces its
own row carrying the reason. These tests assert:

* the route answers 200 and returns every row when one manifest is unusable,
* the unusable manifest still gets a row, marked, naming what is wrong and where to fix it,
* the failure is per manifest, not a property of the catalog as a whole.
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
REAL_MANIFEST = WORKTREE / "model-manifests" / "whisper-turbo.json"

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
"""Ask the real route what it makes of a catalog with two unusable manifests."""
import json
from fastapi.testclient import TestClient

from components.hub import standalone
from components.hub.backend import auth

app = standalone.create_app(start_background=False)
token = auth.load_token()
with TestClient(app) as client:
    response = client.get("/api/hub/model-packages", headers={"X-Hub-Token": token})
payload = {"status": response.status_code,
           "body": response.json() if response.headers.get("content-type", "").startswith(
               "application/json") else response.text}
print("RESULT:" + json.dumps(payload))
'''


def write_catalog(root: Path) -> None:
    """One usable manifest, one with an engine the Hub does not route, one that is not JSON."""
    (root / "whisper-turbo.json").write_text(REAL_MANIFEST.read_text(encoding="utf-8"))
    unusable_engine = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    unusable_engine.update({
        "package_id": "video-thing",
        "model_id": "org/video-thing",
        "engine": "video-mlx",
    })
    (root / "video-thing.json").write_text(json.dumps(unusable_engine))
    (root / "not-json.json").write_text("{ this is not json")


def _voice_worker(port: int = 51102) -> dict:
    return {
        "id": "voice", "title": "Voice Studio", "modality": "voice",
        "host": "127.0.0.1", "port": port, "machine": "local", "local": True,
        "hub_token": "fixture-token", "runtime_identity": {"id": "r", "compatible": True},
        "managed_output_roots": [],
    }


def _subtitle_worker(port: int = 51103) -> dict:
    return {
        "id": "subtitle", "title": "Subtitle Studio", "modality": "transcription",
        "host": "127.0.0.1", "port": port, "machine": "local", "local": True,
        "hub_token": "fixture-token", "runtime_identity": {"id": "r2", "compatible": True},
        "managed_output_roots": [],
    }


CHILD_LANE_CONTRACT = r'''
"""Ask the real route what it publishes for one package's lane.

The route is the one read a scheduler needs; it gains `modality` (from the Hub's engine table)
and the `lane_contract` the studio itself published, with the Hub's own `deadline_seconds`
added. A worker that is down must leave the contract null - never fail the row.
"""
import json
import time
from fastapi.testclient import TestClient

from components.hub import standalone
from components.hub.backend import auth

app = standalone.create_app(start_background=False)
monitor = app.state.monitor
contract = {
    "schema": "studiohub.lane-contract.v1", "modality": "transcription",
    "operation": "audio.transcription",
    "artifact": {"modality": "transcription", "media_type": "application/json",
                 "extension": "json", "per_item": 1},
    "cancel": {"supported": True, "request": "DELETE /api/hub/jobs/{batch_id}"},
}
monitor._catalog_cache["subtitle"] = (time.time(), {"models": [
    {"repo": "mlx-community/whisper-large-v3-turbo", "lane_contract": contract}]})
monitor.status["subtitle"] = {"status": "up"}
token = auth.load_token()
with TestClient(app) as client:
    up = client.get("/api/hub/model-packages", headers={"X-Hub-Token": token})
monitor.status["subtitle"] = {"status": "down"}
with TestClient(app) as client:
    down = client.get("/api/hub/model-packages", headers={"X-Hub-Token": token})
print("RESULT:" + json.dumps({
    "up_status": up.status_code, "up": up.json(),
    "down_status": down.status_code, "down": down.json(),
}))
'''


class ModelPackageIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-packages-")
        root = Path(self.temporary.name)
        self.manifests = root / "model-manifests"
        self.manifests.mkdir()
        write_catalog(self.manifests)
        self.library = root / "model-library"
        self.library.mkdir()
        workers = root / "private-workers.json"
        workers.write_text(json.dumps([_voice_worker()]))
        os.chmod(workers, 0o600)
        release = root / "release.json"
        release.write_text(json.dumps({"version": "0.0.0", "components": {}}))
        self.env = os.environ.copy()
        self.env.update({
            "STUDIOHUB_DATA_DIR": str(root / "state"),
            "STUDIOHUB_WORKERS_FILE": str(workers),
            "STUDIOHUB_MODEL_MANIFESTS": str(self.manifests),
            "STUDIOHUB_MODELS_DIR": str(self.library),
            "STUDIOHUB_PACKAGED": "1",
            "STUDIOHUB_RELEASE_MANIFEST": str(release),
            "PYTHONPATH": os.pathsep.join([str(WORKTREE), os.environ.get("PYTHONPATH", "")])
                         .rstrip(os.pathsep),
        })

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _route(self) -> tuple[int, list[dict]]:
        completed = subprocess.run(
            [_runtime(), "-c", textwrap.dedent(CHILD_FIXTURE)],
            cwd=WORKTREE, env=self.env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(
            completed.returncode, 0,
            "isolated model-packages fixture failed:\nstdout:\n%s\nstderr:\n%s"
            % (completed.stdout, completed.stderr),
        )
        line = next((row for row in reversed(completed.stdout.splitlines())
                     if row.startswith("RESULT:")), None)
        self.assertIsNotNone(line, f"the fixture printed no result:\n{completed.stdout}")
        payload = json.loads(line[len("RESULT:"):])
        models = payload["body"].get("models", []) if isinstance(payload["body"], dict) else []
        return payload["status"], models

    def test_one_unusable_manifest_does_not_take_the_route_down(self) -> None:
        status, models = self._route()
        self.assertEqual(
            status, 200,
            "one unusable manifest answered 503 for the whole catalog; the app reads this "
            "route with a hard try, so every model picker would go blank",
        )
        self.assertEqual(
            sorted(row["package_id"] for row in models),
            ["not-json", "video-thing", "whisper-turbo"],
            "the usable model disappeared because a different manifest was malformed",
        )

    def test_an_unusable_manifest_gets_its_own_row_naming_the_problem(self) -> None:
        _, models = self._route()
        by_id = {row["package_id"]: row for row in models}
        unusable = by_id["video-thing"]
        self.assertTrue(unusable.get("manifest_error"))
        self.assertEqual(unusable["dispatch_state"], "ineligible")
        self.assertIn("video-thing.json", unusable["reason"])
        self.assertIn("engine", unusable["reason"].lower())
        unreadable = by_id["not-json"]
        self.assertTrue(unreadable.get("manifest_error"))
        self.assertIn("not-json.json", unreadable["reason"])

    def test_the_usable_row_is_unaffected(self) -> None:
        _, models = self._route()
        usable = next(row for row in models if row["package_id"] == "whisper-turbo")
        self.assertEqual(usable["model_id"], "mlx-community/whisper-large-v3-turbo")
        self.assertEqual(usable["engine"], "subtitle-mlx")
        self.assertNotIn("manifest_error", usable)
        self.assertEqual(usable["files_state"], "not_installed")
        self.assertIn(usable["dispatch_state"], {"eligible", "ineligible"})

    def test_a_hub_with_no_model_library_still_names_every_package(self) -> None:
        """Whatever is broken, the route answers with rows that say so - never a blank list."""
        self.env.pop("STUDIOHUB_MODELS_DIR", None)
        status, models = self._route()
        self.assertEqual(status, 200)
        self.assertEqual(len(models), 3)
        unusable = next(row for row in models if row["package_id"] == "whisper-turbo")
        self.assertTrue(unusable.get("manifest_error"))
        self.assertIn("STUDIOHUB_MODELS_DIR", unusable["reason"])


class LaneContractRouteTests(unittest.TestCase):
    """The route a scheduler reads carries the lane: modality, contract, and the Hub's deadline."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-lane-contract-")
        root = Path(self.temporary.name)
        self.manifests = root / "model-manifests"
        self.manifests.mkdir()
        write_catalog(self.manifests)
        self.library = root / "model-library"
        self.library.mkdir()
        self.workers = root / "private-workers.json"
        self.workers.write_text(json.dumps([_voice_worker(), _subtitle_worker()]))
        os.chmod(self.workers, 0o600)
        release = root / "release.json"
        release.write_text(json.dumps({"version": "0.0.0", "components": {}}))
        self.env = os.environ.copy()
        self.env.update({
            "STUDIOHUB_DATA_DIR": str(root / "state"),
            "STUDIOHUB_WORKERS_FILE": str(self.workers),
            "STUDIOHUB_MODEL_MANIFESTS": str(self.manifests),
            "STUDIOHUB_MODELS_DIR": str(self.library),
            "STUDIOHUB_PACKAGED": "1",
            "STUDIOHUB_RELEASE_MANIFEST": str(release),
            "PYTHONPATH": os.pathsep.join([str(WORKTREE), os.environ.get("PYTHONPATH", "")])
                         .rstrip(os.pathsep),
        })

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _route(self) -> dict:
        completed = subprocess.run(
            [_runtime(), "-c", textwrap.dedent(CHILD_LANE_CONTRACT)],
            cwd=WORKTREE, env=self.env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(
            completed.returncode, 0,
            "isolated lane-contract fixture failed:\nstdout:\n%s\nstderr:\n%s"
            % (completed.stdout, completed.stderr),
        )
        line = next((row for row in reversed(completed.stdout.splitlines())
                     if row.startswith("RESULT:")), None)
        self.assertIsNotNone(line, f"the fixture printed no result:\n{completed.stdout}")
        return json.loads(line[len("RESULT:"):])

    def test_every_usable_row_publishes_the_modality_of_its_engine(self) -> None:
        payload = self._route()
        self.assertEqual(payload["up_status"], 200)
        by_id = {row["package_id"]: row for row in payload["up"]["models"]}
        self.assertEqual(by_id["whisper-turbo"]["modality"], "transcription")
        # An engine the Hub does not route has no modality to publish, and says so through the
        # row's own manifest_error reason instead.
        self.assertIsNone(by_id["video-thing"]["modality"])

    def test_the_route_echoes_the_studio_contract_and_adds_the_hub_deadline(self) -> None:
        payload = self._route()
        row = next(row for row in payload["up"]["models"]
                   if row["package_id"] == "whisper-turbo")
        contract = row["lane_contract"]
        self.assertIsInstance(contract, dict)
        self.assertEqual(contract["operation"], "audio.transcription")
        self.assertEqual(contract["artifact"]["media_type"], "application/json")
        self.assertEqual(contract["deadline_seconds"], 900.0)

    def test_a_worker_that_is_down_leaves_the_contract_null_and_the_route_up(self) -> None:
        payload = self._route()
        self.assertEqual(payload["down_status"], 200)
        row = next(row for row in payload["down"]["models"]
                   if row["package_id"] == "whisper-turbo")
        self.assertIsNone(row["lane_contract"])
        self.assertEqual(row["modality"], "transcription")


if __name__ == "__main__":
    unittest.main()
