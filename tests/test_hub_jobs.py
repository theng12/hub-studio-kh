"""Isolated ST-02 Hub/Broker proof.

The real loopback worker and all packaged environment imports live in a child
process so this test cannot contaminate the normal Hub configuration suite.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

_PROOF = r'''from __future__ import annotations

import hashlib
import atexit
import asyncio
import httpx
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
import wave
from types import SimpleNamespace


_TEMP = tempfile.TemporaryDirectory(prefix="studiohub-job-proof-")
atexit.register(_TEMP.cleanup)
_ROOT = Path(_TEMP.name)
_DATA = _ROOT / "data"
_LIBRARY = _ROOT / "models"
_MANIFESTS = _ROOT / "manifests"
_WORKERS = _ROOT / "workers.json"
_TOKEN = "fixture-existing-hub-token"
for directory in (_DATA, _LIBRARY, _MANIFESTS):
    directory.mkdir(mode=0o700)
(_DATA / ".hub_token").write_text(_TOKEN + "\n")
os.chmod(_DATA / ".hub_token", 0o600)

# These variables have to exist before the adopted modules bind DATA_DIR.
os.environ.update({
    "STUDIOHUB_DATA_DIR": str(_DATA),
    "STUDIOHUB_WORKERS_FILE": str(_WORKERS),
    "STUDIOHUB_MODELS_DIR": str(_LIBRARY),
    "STUDIOHUB_MODEL_MANIFESTS": str(_MANIFESTS),
    "STUDIOHUB_PACKAGED": "1",
    "STUDIOHUB_REQUIRE_AUTH": "1",
})
# standalone creates its module-level app on import, before the fixture server
# has selected its dynamic port.  A private placeholder lets that construction
# remain side-effect-free; setUpClass replaces it with the running descriptor.
_WORKERS.write_text(json.dumps([{
    "id": "voice", "modality": "voice", "host": "127.0.0.1", "port": 51101,
    "machine": "local", "local": True, "runtime_identity": {"compatible": True}, "hub_token": _TOKEN,
}]))
os.chmod(_WORKERS, 0o600)

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import uvicorn  # noqa: E402

from components import model_packages  # noqa: E402
from components.hub import standalone  # noqa: E402
from components.hub.backend import broker, control_plane, execution_identity, ledger, peers, transcription_jobs  # noqa: E402


def _wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 80)
    return output.getvalue()


_AUDIO = _wav()
_AUDIO_SHA = hashlib.sha256(_AUDIO).hexdigest()


class FixtureWorker:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.transcription_posts = 0
        self.transcription_completed = 0
        self.transcription_refusal = None
        self.transcription_started = threading.Event()
        self.app = FastAPI()
        self._routes()
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        self.port = probe.getsockname()[1]
        probe.close()
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port,
                                                    log_level="error", access_log=False))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def _check(self, request: Request) -> None:
        if request.headers.get("x-studio-token") != _TOKEN:
            raise HTTPException(401)

    def _routes(self) -> None:
        @self.app.get("/api/health")
        async def health():
            return {"ok": True, "app_version": "1.20.13"}

        @self.app.get("/api/fleet/activity")
        async def activity(request: Request):
            self._check(request)
            return {"schema": 1, "studio": "voice", "observed_at": time.time(), "active": []}

        @self.app.get("/api/catalog")
        async def catalog(request: Request):
            self._check(request)
            return {"models": [
                {"repo": "fixture/voice", "cache": {"state": "cached"}, "runtime_ready": True,
                 "available": True, "execution_ready": True},
                {"repo": "fixture/blocked", "cache": {"state": "cached"}, "runtime_ready": True,
                 "available": True, "execution_ready": False},
            ]}

        @self.app.post("/api/generate/txt2speech")
        async def generate(request: Request):
            self._check(request)
            body = await request.json()
            with self.lock:
                job_id = f"voice-{len(self.jobs) + 1}"
                self.jobs[job_id] = {"state": "running" if body.get("text") == "hold" else "done"}
            return {"job": {"id": job_id}}

        @self.app.get("/api/generate/jobs/{job_id}")
        async def job(job_id: str, request: Request):
            self._check(request)
            with self.lock:
                state = self.jobs.get(job_id, {}).get("state")
            if state is None:
                raise HTTPException(404)
            if state == "running":
                return {"job": {"id": job_id, "state": "running"}}
            if state == "cancelled":
                return {"job": {"id": job_id, "state": "cancelled", "error": "cancelled"}}
            return {"job": {"id": job_id, "state": "done", "output_url": f"/api/generate/jobs/{job_id}/audio",
                             "bytes": len(_AUDIO), "sha256": _AUDIO_SHA, "media_type": "audio/wav"}}

        @self.app.delete("/api/generate/jobs/{job_id}")
        async def cancel(job_id: str, request: Request):
            self._check(request)
            with self.lock:
                if job_id in self.jobs:
                    self.jobs[job_id]["state"] = "cancelled"
            return {"ok": True}

        @self.app.get("/api/generate/jobs/{job_id}/audio")
        async def audio(job_id: str, request: Request):
            self._check(request)
            if job_id not in self.jobs:
                raise HTTPException(404)
            return Response(_AUDIO, media_type="audio/wav")

        @self.app.post("/api/transcribe")
        async def transcribe(request: Request):
            self._check(request)
            async with request.form():
                pass
            with self.lock:
                self.transcription_posts += 1
                refusal = self.transcription_refusal
            self.transcription_started.set()
            if refusal:
                raise HTTPException(refusal, "fixture known worker refusal")
            await asyncio.sleep(.65)  # inference continues after a Hub client drops
            with self.lock:
                self.transcription_completed += 1
            return {"srt": "1\n00:00:00,000 --> 00:00:00,100\nfixture\n",
                    "studio_task_id": "fixture-subtitle-task"}

    def start(self) -> None:
        self.thread.start()
        deadline = time.time() + 5
        while not self.server.started:
            if time.time() >= deadline:
                raise RuntimeError("fixture worker did not start")
            time.sleep(.01)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


def _manifest(package_id: str, model_id: str, engine: str = "voice-mlx") -> dict:
    payload = b"fixture-model\n"
    source = _ROOT / f"source-{package_id}"
    source.mkdir(exist_ok=True)
    (source / "model.bin").write_bytes(payload)
    result = {
        "schema_version": 1, "package_id": package_id, "model_id": model_id,
        "revision": "a" * 40, "engine": engine, "minimum_macos": "0.0",
        "files": [{"path": "model.bin", "size": len(payload),
                   "sha256": hashlib.sha256(payload).hexdigest(), "urls": []}],
    }
    (_MANIFESTS / f"{package_id}.json").write_text(json.dumps(result))
    model_packages.import_folder(result, source, _LIBRARY)
    return result


class HubJobProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.worker = FixtureWorker()
        cls.worker.start()
        _WORKERS.write_text(json.dumps([{
            "id": "voice", "modality": "voice", "title": "Fixture Voice",
            "host": "127.0.0.1", "port": cls.worker.port, "machine": "local", "local": True,
            "runtime_identity": {"id": "fixture-runtime", "compatible": True}, "hub_token": _TOKEN,
        }]))
        os.chmod(_WORKERS, 0o600)
        _manifest("fixture-voice", "fixture/voice")
        _manifest("fixture-blocked", "fixture/blocked")
        cls.old_poll = broker.POLL_S
        broker.POLL_S = .05

    @classmethod
    def tearDownClass(cls) -> None:
        broker.POLL_S = cls.old_poll
        cls.worker.stop()

    def setUp(self) -> None:
        broker.batches.clear()
        broker._busy.clear()
        broker._reserved["gb"] = 0.0
        for path in _DATA.glob("hub.db*"):
            path.unlink()
        ledger.prepare_database()

    def _admit_fixture_catalog(self, app) -> None:
        monitor = app.state.monitor
        now = time.time()
        monitor.status["voice"] = {"status": "up", "app_version": "1.20.13"}
        monitor._catalog_cache["voice"] = (now, {
            "models": [
                {"repo": "fixture/voice", "cache": {"state": "cached"}, "runtime_ready": True,
                 "available": True, "execution_ready": True},
                {"repo": "fixture/blocked", "cache": {"state": "cached"}, "runtime_ready": True,
                 "available": True, "execution_ready": False},
            ]
        })
        monitor._catalog_meta["voice"] = {
            "last_attempt_at": now, "last_success_at": now, "last_error": None,
            "catalog_last_attempt_at": now, "catalog_last_success_at": now, "catalog_last_error": None,
        }
        broker.wake_dispatcher()

    def _wait_for(self, client: TestClient, batch_id: str, state: str, timeout: float = 4) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            payload = client.get(f"/api/hub/jobs/{batch_id}", headers={"Authorization": f"Bearer {_TOKEN}"}).json()
            if payload["items"][0]["state"] == state:
                return payload
            time.sleep(.03)
        self.fail(f"job {batch_id} did not reach {state}: {payload}")

    def test_image_reconciliation_route_requires_owner_and_never_submits(self) -> None:
        from unittest.mock import AsyncMock, patch
        app = standalone.create_app(start_background=False)
        path = "/api/hub/jobs/original-image/items/0/reconcile"
        reply = {"batch_id": "original-image", "client_request_id": "original-request",
                 "reconciled": False, "item": {"state": "uncertain"}}
        with TestClient(app) as client, patch.object(broker, "reconcile_image_item", new=AsyncMock(return_value=reply)) as reconcile:
            self.assertEqual(client.post(path).status_code, 401)
            reconcile.assert_not_awaited()
            response = client.post(path, headers={"Authorization": f"Bearer {_TOKEN}"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), reply)
            reconcile.assert_awaited_once_with("original-image", 0, client=app.state.monitor._client)
            reconcile.side_effect = ValueError("unknown Image job")
            self.assertEqual(client.post(path, headers={"Authorization": f"Bearer {_TOKEN}"}).status_code, 400)

    def test_authenticated_lifecycle_idempotency_cancel_admission_and_recovery(self) -> None:
        app = standalone.create_app(start_background=True)
        headers = {"Authorization": f"Bearer {_TOKEN}"}
        with TestClient(app) as client:
            self.assertEqual(client.post("/api/hub/jobs", json={}, headers=headers).status_code, 400)
            self._admit_fixture_catalog(app)
            envelope = {"modality": "voice", "model": "fixture/voice", "clientRequestId": "lost-response-0001",
                        "items": [{"text": "hello"}]}
            accepted = client.post("/api/hub/jobs", json=envelope, headers=headers)
            self.assertEqual(accepted.status_code, 200)
            batch_id = accepted.json()["batch_id"]
            replay = client.post("/api/hub/jobs", json=envelope, headers=headers)
            self.assertEqual(replay.json(), {"batch_id": batch_id, "items": 1, "replayed": True})
            done = self._wait_for(client, batch_id, "done")
            self.assertEqual(done["items"][0]["terminal_result"]["status"], "succeeded")
            # The app refuses to play or save a voice clip it cannot check, so the completed
            # item has to publish the byte count and digest the artifact is verified against.
            # They come from the Hub decoding the WAV itself when it adopts the finished job.
            self.assertEqual(done["items"][0]["bytes"], len(_AUDIO), done["items"][0])
            self.assertEqual(done["items"][0]["sha256"], _AUDIO_SHA, done["items"][0])
            self.assertEqual(done["items"][0]["media_type"], "audio/wav")
            artifact = client.get(done["items"][0]["artifact_url"], headers=headers)
            self.assertEqual((artifact.status_code, artifact.content), (200, _AUDIO))
            self.assertEqual(hashlib.sha256(artifact.content).hexdigest(), _AUDIO_SHA)

            held = client.post("/api/hub/jobs", json={"modality": "voice", "model": "fixture/voice",
                "clientRequestId": "cancel-0001", "items": [{"text": "hold"}]}, headers=headers).json()["batch_id"]
            self._wait_for(client, held, "running")
            self.assertEqual(client.delete(f"/api/hub/jobs/{held}", headers=headers).status_code, 200)
            self._wait_for(client, held, "cancelled")

            # Native and external callers race through the same authenticated
            # Hub route and adopted Broker lock, with one physical worker slot.
            barrier = threading.Barrier(3)
            results: list[dict] = []
            def submit(n: int) -> None:
                barrier.wait()
                response = client.post("/api/hub/jobs", json={
                    "modality": "voice", "model": "fixture/voice",
                    "clientRequestId": f"parallel-{n:04d}", "items": [{"text": "hold"}]},
                    headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                results.append(response.json())
            threads = [threading.Thread(target=submit, args=(n,)) for n in (1, 2)]
            [thread.start() for thread in threads]
            barrier.wait()
            [thread.join() for thread in threads]
            self.assertEqual(len({row["batch_id"] for row in results}), 2)
            self._admit_fixture_catalog(app)
            # The scheduler's bounded heartbeat observes the durable writes.
            time.sleep(3.2)
            self.assertEqual(sum(item["state"] == "running" for batch in broker.batches.values()
                                 for item in batch["items"]), 1)
            for row in results:
                client.delete(f"/api/hub/jobs/{row['batch_id']}", headers=headers)

            blocked = client.post("/api/hub/jobs", json={"modality": "voice", "model": "fixture/blocked",
                "clientRequestId": "blocked-0001", "items": [{"text": "hello"}]}, headers=headers).json()["batch_id"]
            time.sleep(.15)
            waited = client.get(f"/api/hub/jobs/{blocked}", headers=headers).json()
            self.assertEqual(waited["items"][0]["state"], "queued")
            self.assertIn("execution requirements are not ready", waited["governor_note"])

        # A persisted accepted Voice attempt is never silently retried after restart.
        app = standalone.create_app(start_background=False)
        with TestClient(app) as client:
            response = client.post("/api/hub/jobs", json={"modality": "voice", "model": "fixture/voice",
                "clientRequestId": "restart-0001", "items": [{"text": "hello"}]}, headers=headers)
            batch = broker.batches[response.json()["batch_id"]]
            batch["items"][0].update(state="running", studio="voice", studio_job_id="accepted-before-restart", tries=1)
            ledger.save_batch(batch)
            broker.batches.clear()
            broker.restore_batches()
            restored = ledger.load_batch(batch["id"])
            self.assertEqual(restored["items"][0]["state"], "uncertain")
            self.assertEqual(restored["items"][0]["tries"], 1)

    def test_packaged_subtitle_is_independent_and_restored_work_stays_gated(self) -> None:
        _manifest("fixture-subtitle", "fixture/subtitle", "subtitle-mlx")
        voice = json.loads(_WORKERS.read_text())[0]
        subtitle = {**voice, "id": "subtitle", "modality": "transcription",
                    "port": voice["port"] + 1, "machine": "physical-node-a",
                    "runtime_identity": {"id": "fixture-subtitle-runtime", "compatible": True}}

        async def scheduling(studio):
            return {"available": True, "models": [{"repo": "fixture/subtitle", "cached": True}]}

        monitor = SimpleNamespace(registry=[voice], status={"voice": {"status": "up"}},
                                  scheduling_transcription=scheduling)
        envelope = {"modality": "transcription", "model": "fixture/subtitle"}
        broker.configure_admission_guard(lambda batch: standalone._submission_readiness(batch, monitor))
        self.assertEqual(broker.admission_refusal(envelope)["code"], "MODEL_NOT_READY")

        monitor.registry.append(subtitle)
        monitor.status["subtitle"] = {"status": "up"}
        self.assertIsNone(broker.admission_refusal(envelope))
        self.assertEqual([row["id"] for row in asyncio.run(
            transcription_jobs._eligible_studios(monitor, "fixture/subtitle"))], ["subtitle"])
        self.assertTrue(peers.dispatch_ready(subtitle))

        # A restored batch passed an earlier submit gate, but its runtime may
        # become incompatible after an app/OS change. It must keep its original
        # queued item and attempt ID without contacting Voice as a fallback.
        subtitle["runtime_identity"]["compatible"] = False
        batch = {"id": "restored-subtitle", "created_at": time.time(),
                 "idempotency_key": "fixture-restored-subtitle", "model": "fixture/subtitle",
                 "modality": "transcription", "items": [{"index": 0, "state": "queued", "tries": 0}]}
        transcription_jobs.batches[batch["id"]] = batch
        self.assertFalse(asyncio.run(transcription_jobs.has_dispatchable_work(
            monitor, "physical-node-a")))
        self.assertEqual(asyncio.run(transcription_jobs.dispatch_once(monitor)), 0)
        self.assertEqual((batch["items"][0]["state"], batch["items"][0]["tries"]),
                         ("queued", 0))
        self.assertIn("runtime, OS, or chip", batch["governor_note"])
        transcription_jobs.batches.clear()

    def test_packaged_subtitle_running_attempt_is_not_replayed_after_restart(self) -> None:
        input_path = _ROOT / "accepted-subtitle.wav"
        input_path.write_bytes(_AUDIO)
        batch = {"id": "accepted-subtitle", "idempotency_key": "accepted-subtitle-key",
                 "created_at": time.time(), "model": "fixture/subtitle", "cancelled": False,
                 "items": [{"index": 0, "item_id": "clip-1", "filename": "clip.wav",
                            "input_path": str(input_path), "state": "running", "tries": 1,
                            "studio": "subtitle", "studio_task_id": "stt-accepted-subtitle-0"}]}
        transcription_jobs._save(batch)
        transcription_jobs.batches.clear()
        self.assertEqual(transcription_jobs.restore_batches(), 1)
        restored = transcription_jobs.batches[batch["id"]]["items"][0]
        self.assertEqual((restored["state"], restored["studio"], restored["studio_task_id"],
                          restored["tries"]),
                         ("uncertain", "subtitle", "stt-accepted-subtitle-0", 1))
        self.assertTrue(restored["manual_action_required"])
        self.assertEqual(transcription_jobs.retry_batch(batch["id"])[1], 0)
        self.assertEqual(transcription_jobs.summary(transcription_jobs.batches[batch["id"]])["status"],
                         "uncertain")
        self.assertEqual(asyncio.run(transcription_jobs.cancel_batch(batch["id"]))["items"][0]["state"],
                         "uncertain")
        transcription_jobs.batches.clear()

    def test_genstudio_subtitle_replay_precedes_current_model_readiness(self) -> None:
        _manifest("fixture-subtitle", "fixture/subtitle", "subtitle-mlx")
        previous = _WORKERS.read_text()
        worker = json.loads(previous)[0]
        subtitle = {**worker, "id": "subtitle", "modality": "transcription",
                    "runtime_identity": {"id": "fixture-subtitle-runtime", "compatible": True}}
        _WORKERS.write_text(json.dumps([subtitle]))
        try:
            app = standalone.create_app(start_background=False)
            headers = {"Authorization": f"Bearer {_TOKEN}"}
            identity = {"genstudio_job_id": "job-subtitle-1", "genstudio_attempt_id": "attempt-1",
                        "idempotency_key": "exact-subtitle-1", "fencing_token": 1,
                        "site_id": control_plane.load_settings()["site_id"],
                        "operation": "audio.transcription", "model_revision": "a" * 40,
                        "contract_hash": "sha256:" + "b" * 64}

            def submit(language=None):
                data = {"item_ids": "clip-1", "model": "fixture/subtitle",
                        "genstudio_execution": json.dumps(identity)}
                if language:
                    data["language"] = language
                return client.post("/api/hub/transcription/jobs", headers=headers, data=data,
                                   files={"files": ("clip.wav", _AUDIO, "audio/wav")})

            with TestClient(app) as client:
                first = submit()
                self.assertEqual(first.status_code, 200, first.text)
                batch_id = first.json()["batch_id"]
                app.state.monitor.registry[0]["runtime_identity"]["compatible"] = False
                replay = submit()
                self.assertEqual((replay.status_code, replay.json().get("batch_id")), (200, batch_id))
                self.assertTrue(replay.json()["duplicate"])
                conflict = submit(language="fr")
                self.assertEqual(conflict.status_code, 409)
                self.assertIn("different GenStudio assignment", conflict.text)
                identity = {**identity, "genstudio_job_id": "job-subtitle-2",
                            "genstudio_attempt_id": "attempt-2", "idempotency_key": "new-subtitle-2"}
                refused_new = submit()
                self.assertEqual(refused_new.status_code, 409)
                with execution_identity._conn() as connection:
                    self.assertEqual(connection.execute(
                        "SELECT COUNT(*) FROM genstudio_idempotency WHERE genstudio_job_id = ?",
                        (identity["genstudio_job_id"],)).fetchone()[0], 0)
                self.assertEqual(len(transcription_jobs.list_batches()), 1)
        finally:
            _WORKERS.write_text(previous)
            transcription_jobs.batches.clear()

    def test_real_subtitle_lost_response_and_cancel_do_not_duplicate_post(self) -> None:
        studio = {"id": "subtitle", "modality": "transcription", "host": "127.0.0.1",
                  "port": self.worker.port, "machine": "local", "local": True,
                  "studio_token": _TOKEN, "hub_token": _TOKEN}
        input_path = _ROOT / "uncertain-subtitle.wav"
        input_path.write_bytes(_AUDIO)

        def batch_for(batch_id):
            return {"id": batch_id, "idempotency_key": batch_id + "-key",
                    "created_at": time.time(), "model": "fixture/subtitle", "cancelled": False,
                    "items": [{"index": 0, "item_id": "clip-1", "filename": "clip.wav",
                               "input_path": str(input_path), "state": "running", "tries": 1,
                               "studio": "subtitle", "studio_task_id": f"stt-{batch_id}-0"}]}

        async def lost_response():
            async with httpx.AsyncClient() as real:
                class LostClient:
                    async def post(self, *args, **kwargs):
                        response = await real.post(*args, **kwargs)
                        self_response_status = response.status_code
                        assert self_response_status == 200
                        raise httpx.ReadError("worker completed but its response was lost")

                monitor = SimpleNamespace(_client=LostClient())
                batch = batch_for("lost-subtitle-response")
                transcription_jobs.batches[batch["id"]] = batch
                await transcription_jobs._run_item(
                    monitor, batch, batch["items"][0], studio, "lost-owner")
                return batch

        with self.worker.lock:
            self.worker.transcription_posts = self.worker.transcription_completed = 0
        self.worker.transcription_started.clear()
        lost = asyncio.run(lost_response())
        self.assertEqual((lost["items"][0]["state"], lost["items"][0]["tries"]),
                         ("uncertain", 1))
        self.assertEqual((self.worker.transcription_posts, self.worker.transcription_completed), (1, 1))
        transcription_jobs.batches.clear()

        transcription_jobs.restore_batches()
        self.assertEqual(transcription_jobs.retry_batch(lost["id"])[1], 0)
        self.assertEqual(transcription_jobs.batches[lost["id"]]["items"][0]["studio_task_id"],
                         "stt-lost-subtitle-response-0")
        self.assertEqual(self.worker.transcription_posts, 1)

        async def cancel_after_acceptance():
            async with httpx.AsyncClient() as real:
                monitor = SimpleNamespace(_client=real)
                batch = batch_for("cancel-subtitle-after-post")
                batch["items"].append({"index": 1, "item_id": "clip-2", "filename": "clip.wav",
                                       "input_path": str(input_path), "state": "queued", "tries": 0})
                transcription_jobs.batches[batch["id"]] = batch
                task = asyncio.create_task(transcription_jobs._run_item(
                    monitor, batch, batch["items"][0], studio, "cancel-owner"))
                transcription_jobs._item_tasks[(batch["id"], 0)] = task
                for _ in range(100):
                    if self.worker.transcription_posts == 2: break
                    await asyncio.sleep(.02)
                assert self.worker.transcription_posts == 2
                await transcription_jobs.cancel_batch(batch["id"])
                await asyncio.gather(task, return_exceptions=True)
                return batch

        cancelled = asyncio.run(cancel_after_acceptance())
        self.assertEqual([item["state"] for item in cancelled["items"]],
                         ["uncertain", "cancelled"])
        self.assertEqual([item["tries"] for item in cancelled["items"]], [1, 0])
        self.assertEqual(transcription_jobs.summary(cancelled)["status"], "uncertain")
        time.sleep(.75)
        self.assertEqual((self.worker.transcription_posts, self.worker.transcription_completed), (2, 2))
        transcription_jobs.batches.clear()

    def test_subtitle_index_error_preserves_completed_result(self) -> None:
        studio = {"id": "subtitle", "modality": "transcription", "host": "127.0.0.1",
                  "port": self.worker.port, "machine": "local", "local": True,
                  "studio_token": _TOKEN, "hub_token": _TOKEN}
        input_path = _ROOT / "storage-subtitle.wav"
        input_path.write_bytes(_AUDIO)
        batch = {"id": "storage-subtitle", "idempotency_key": "storage-subtitle-key",
                 "created_at": time.time(), "model": "fixture/subtitle", "cancelled": False,
                 "items": [{"index": 0, "item_id": "clip-storage", "filename": "clip.wav",
                            "input_path": str(input_path), "state": "running", "tries": 1,
                            "studio": "subtitle", "studio_task_id": "stt-storage-0"}]}
        (transcription_jobs.ROOT / batch["id"] / "output").mkdir(parents=True)

        async def run_with_failed_index():
            from components.hub import output_storage
            original = output_storage.register_finalized_output
            def fail_index(*args, **kwargs):
                raise OSError("index unavailable")
            output_storage.register_finalized_output = fail_index
            try:
                async with httpx.AsyncClient() as real:
                    await transcription_jobs._run_item(
                        SimpleNamespace(_client=real, _transcribe_cache={}),
                        batch, batch["items"][0], studio, "storage-owner")
            finally:
                output_storage.register_finalized_output = original

        asyncio.run(run_with_failed_index())
        item = batch["items"][0]
        self.assertEqual(item["state"], "done")
        self.assertIn("OSError: index unavailable", item.get("output_storage_error", ""))
        self.assertTrue(Path(item["artifact_path"]).is_file())

    def test_packaged_subtitle_dispatcher_skips_legacy_cleanup_timer(self) -> None:
        async def check():
            monitor = SimpleNamespace(registry=[], status={})
            transcription_jobs.start_dispatcher(monitor)
            self.assertIsNotNone(transcription_jobs._dispatcher_task)
            self.assertIsNone(transcription_jobs._cleanup_task)
            await transcription_jobs.stop()

            packaged = os.environ.pop("STUDIOHUB_PACKAGED")
            try:
                transcription_jobs.start_dispatcher(monitor)
                self.assertIsNotNone(transcription_jobs._cleanup_task)
                await transcription_jobs.stop()
            finally:
                os.environ["STUDIOHUB_PACKAGED"] = packaged

        asyncio.run(check())

    def test_uncertain_subtitle_holds_its_physical_machine_after_hub_restart(self) -> None:
        machine = "physical-node-a"
        broker.configure_monitor(SimpleNamespace(registry=[
            {"id": "subtitle", "modality": "transcription", "machine": "renamed-node", "local": True},
        ]))
        input_path = _ROOT / "fenced-subtitle.wav"
        input_path.write_bytes(_AUDIO)
        batch = {"id": "fenced-subtitle", "idempotency_key": "fenced-subtitle-key",
                 "created_at": time.time(), "model": "fixture/subtitle", "cancelled": False,
                 "items": [{"index": 0, "item_id": "clip-1", "filename": "clip.wav",
                            "input_path": str(input_path), "state": "running", "tries": 1,
                            "studio": "subtitle", "machine": machine,
                            "studio_task_id": "stt-fenced-subtitle-0"}]}
        transcription_jobs._save(batch)
        transcription_jobs.batches.clear()
        broker._external_machine_leases.clear()
        transcription_jobs.restore_batches()
        self.assertEqual(transcription_jobs.batches[batch["id"]]["items"][0]["state"],
                         "uncertain")
        self.assertIn(machine, broker.busy_machines())
        for owner in ("image:next", "voice:next", "transcription:next"):
            self.assertFalse(broker.acquire_external_machine(machine, owner))
        self.assertTrue(broker.acquire_external_machine("physical-node-b", "image:other"))
        broker.release_external_machine("physical-node-b", "image:other")
        transcription_jobs.batches.clear()

    def test_known_subtitle_http_refusals_do_not_hold_a_physical_machine(self) -> None:
        studio = {"id": "subtitle", "modality": "transcription", "host": "127.0.0.1",
                  "port": self.worker.port, "machine": "local", "local": True,
                  "studio_token": _TOKEN, "hub_token": _TOKEN}
        input_path = _ROOT / "refused-subtitle.wav"
        input_path.write_bytes(_AUDIO)

        async def request(status):
            async with httpx.AsyncClient() as real:
                monitor = SimpleNamespace(_client=real, _transcribe_cache={})
                batch = {"id": f"refused-subtitle-{status}",
                         "idempotency_key": f"refused-subtitle-{status}-key",
                         "created_at": time.time(), "model": "fixture/subtitle", "cancelled": False,
                         "items": [{"index": 0, "item_id": "clip-1", "filename": "clip.wav",
                                    "input_path": str(input_path), "state": "running", "tries": 1,
                                    "studio": "subtitle", "machine": "local",
                                    "studio_task_id": f"stt-refused-{status}-0"}]}
                transcription_jobs.batches[batch["id"]] = batch
                await transcription_jobs._run_item(
                    monitor, batch, batch["items"][0], studio, f"refused-owner-{status}")
                return batch["items"][0]

        try:
            for status in (400, 401, 409, 503):
                with self.worker.lock:
                    self.worker.transcription_refusal = status
                item = asyncio.run(request(status))
                self.assertEqual(item["state"], "queued" if status in (409, 503) else "error")
                self.assertFalse(item.get("manual_action_required"))
                self.assertNotIn("local", broker.busy_machines())
            for status in (408, 504):
                with self.worker.lock:
                    self.worker.transcription_refusal = status
                item = asyncio.run(request(status))
                self.assertEqual(item["state"], "uncertain")
                self.assertTrue(item["manual_action_required"])
                self.assertIn("local", broker.busy_machines())
                transcription_jobs.batches.clear()
        finally:
            with self.worker.lock:
                self.worker.transcription_refusal = None
            transcription_jobs.batches.clear()


import unittest
unittest.main()
'''


class HubJobProofTests(unittest.TestCase):
    def test_real_broker_lifecycle_proof(self) -> None:
        env = dict(os.environ)
        root = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        result = subprocess.run([sys.executable, "-B", "-c", _PROOF],
                                text=True, capture_output=True, env=env, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
