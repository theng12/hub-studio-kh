"""Isolated ST-02 Hub/Broker proof.

The real loopback worker and all packaged environment imports live in a child
process so this test cannot contaminate the normal Hub configuration suite.
"""
from __future__ import annotations

import ast
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

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
_REFUSAL = ("SectionQualityError: Section 6 of 7 came out silent on all 4 attempts, so the audio "
            "was not delivered (attempt 4: voiced 2.54 s < 4.06 s). Try again, or reword that part "
            "of the text.")
_FINAL_ROW = {"section": 6, "attempt": 4, "transcribe_back": "script", "voiced_s": 2.54,
              "min_voiced_s": 4.06, "characters": 203, "accepted": False,
              "rejection_code": "TTS_SECTION_SILENT", "check_seconds": 0.012}


def _review_receipt(job_id: str, request_id: str, final: dict | None = None, **changes) -> dict:
    """A Voice worker's review receipt (`generation._review_receipt`), as the Hub receives it."""
    final = dict(_FINAL_ROW if final is None else final)
    body = {
        "schema": "studiohub.rejected-voice-review-candidate.v1", "active": False,
        "disposition": "review_only", "worker_job_id": job_id, "client_request_id": request_id,
        "section": {"index": 6, "total": 7, "attempt": 4, "text": "fixture section"},
        "quality": {"revision": "voicestudio.tts-section-quality.v3",
                    "rejection_code": "TTS_SECTION_SILENT", "final": final},
        "audio": {"media_type": "audio/wav", "bytes": len(_AUDIO), "sha256": _AUDIO_SHA},
        "terminal": {"state": "error", "rejection_code": "TTS_SECTION_SILENT",
                     "final_quality_sha256": broker.review_sha256(final)},
        **changes,
    }
    return {**body, "receipt_sha256": broker.review_sha256(body)}






class FixtureWorker:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.transcription_posts = 0
        self.transcription_completed = 0
        self.transcription_refusal = None
        self.transcription_started = threading.Event()
        self.review: dict[str, dict] = {}   # worker job id -> its published review receipt
        # Like the real worker, a refused job shows `error` before its receipt is durable: these
        # many reads answer 409 `review_candidate_pending` first.
        self.review_pending: dict[str, int] = {}
        self.review_reads: dict[str, int] = {}
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
                refused = body.get("text") == "refuse"
                job_id = f"{len(self.jobs) + 1:012x}" if refused else f"voice-{len(self.jobs) + 1}"
                self.jobs[job_id] = {
                    "state": "error" if refused else "running" if body.get("text") == "hold" else "done",
                    "params": body,
                }
                if refused:   # the real worker publishes this after its terminal error is durable
                    self.review[job_id] = _review_receipt(job_id, body["client_request_id"])
                    self.review_pending[job_id] = 2
            # Match GenerationJob.serialize() at acceptance: the worker has
            # accepted the params, but a terminal artifact is not available yet.
            return {"job": {"id": job_id, "mode": "txt2speech", "state": "queued",
                             "params": body, "output_path": None, "output_url": None}}

        @self.app.get("/api/generate/jobs/{job_id}")
        async def job(job_id: str, request: Request):
            self._check(request)
            with self.lock:
                record = self.jobs.get(job_id, {})
                state = record.get("state")
            if state is None:
                raise HTTPException(404)
            if state == "running":
                return {"job": {"id": job_id, "mode": "txt2speech", "state": "running",
                                 "params": record["params"], "output_path": None,
                                 "output_url": None}}
            if state == "cancelled":
                return {"job": {"id": job_id, "mode": "txt2speech", "state": "cancelled",
                                 "params": record["params"], "output_path": None,
                                 "output_url": None, "error": "cancelled"}}
            if state == "error":
                return {"job": {"id": job_id, "mode": "txt2speech", "state": "error",
                                 "params": record["params"], "output_path": None,
                                 "output_url": None, "error": _REFUSAL,
                                 "error_code": "TTS_SECTION_SILENT",
                                 "section_quality": {"revision": "voicestudio.tts-section-quality.v3",
                                                     "sections": [dict(_FINAL_ROW)]}}}
            return {"job": {"id": job_id, "mode": "txt2speech", "state": "done",
                             "params": record["params"],
                             "output_path": str(_ROOT / f"{job_id}.wav"),
                             "output_url": f"/api/generate/jobs/{job_id}/audio",
                             "bytes": len(_AUDIO), "sha256": _AUDIO_SHA,
                             "media_type": "audio/wav"}}

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

        @self.app.get("/api/fleet/jobs/{job_id}/review-candidate")
        async def review_candidate(job_id: str, request: Request):
            self._check(request)
            with self.lock:
                self.review_reads[job_id] = self.review_reads.get(job_id, 0) + 1
                waiting = self.review_pending.get(job_id, 0)
                if waiting:
                    self.review_pending[job_id] = waiting - 1 if waiting > 0 else waiting
                receipt = self.review.get(job_id)
            if waiting:
                raise HTTPException(409, {"code": "review_candidate_pending"})
            if receipt is None:
                raise HTTPException(404, {"code": "review_candidate_not_found"})
            return {"receipt": receipt,
                    "media": {"handle": f"{job_id}handle.sig", "expires_at": time.time() + 300}}

        @self.app.get("/api/fleet/jobs/{job_id}/review-candidate/media/{handle}")
        async def review_media(job_id: str, handle: str, request: Request):
            self._check(request)
            if job_id not in self.review or handle != f"{job_id}handle.sig":
                raise HTTPException(404)
            return Response(_AUDIO, media_type="audio/wav",
                            headers={"X-Content-SHA256": _AUDIO_SHA})

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
    if engine == "subtitle-mlx":
        # A subtitle manifest must declare what its model actually does; the central validator
        # refuses one without the block, so a fixture that omits it never reaches the worker.
        # This is the embedded twin of `tests/_hub_dispatch_proof.py`; both must carry it.
        result["subtitle"] = {
            "family": "whisper", "languages": ["multilingual"],
            "segment_timestamps": True, "word_timestamps": True, "long_form": True,
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

            # The operator's free-memory override (`PUT /api/hub/memory/admission`, how the 8 GB runner
            # measures a model the guard would refuse) is forwarded to the worker's own preflight, and
            # only while it is set.
            from components.hub.backend import memory_admission
            route = "/api/hub/memory/admission"
            self.assertEqual(client.put(route, json={}, headers=headers).status_code, 400)
            self.assertEqual(client.put(route, json={"model": "no/such", "min_free_memory_gb": 1},
                                        headers=headers).status_code, 404)
            try:
                set_ = client.put(route, json={"model": "fixture/voice", "min_free_memory_gb": 0.5}, headers=headers)
                self.assertEqual(set_.status_code, 200, set_.text)
                self.assertEqual(set_.json()["effective_min_free_memory_gb"], 0.5)
                self.assertTrue(client.get(route, params={"model": "fixture/voice"}, headers=headers).json()["overridden"])
                first = client.post("/api/hub/jobs", json={"modality": "voice", "model": "fixture/voice",
                    "clientRequestId": "override-0001", "items": [{"text": "hello"}]}, headers=headers).json()["batch_id"]
                self._wait_for(client, first, "done")
                self.assertEqual(list(self.worker.jobs.values())[-1]["params"].get("memory_floor_gb"), 0.5)
                cleared = client.put(route, json={"model": "fixture/voice", "min_free_memory_gb": None}, headers=headers)
                self.assertFalse(cleared.json()["overridden"])
                second = client.post("/api/hub/jobs", json={"modality": "voice", "model": "fixture/voice",
                    "clientRequestId": "override-0002", "items": [{"text": "hello"}]}, headers=headers).json()["batch_id"]
                self._wait_for(client, second, "done")
                self.assertNotIn("memory_floor_gb", list(self.worker.jobs.values())[-1]["params"])
            finally:
                memory_admission.reset_override("fixture/voice", {})

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

        # A persisted accepted Voice attempt runs again after a restart under a fresh request id
        # (owner, 2026-10-05), and the original is watched so a finished one is adopted.
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
            self.assertEqual(restored["items"][0]["state"], "queued")
            self.assertEqual(restored["items"][0]["tries"], 1)
            self.assertTrue(restored["items"][0]["client_request_id"].endswith(":rerun-1"))
            self.assertEqual([row[3] for row in broker._restored_attempts], ["accepted-before-restart"])
            broker._restored_attempts.clear()

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

    def test_packaged_subtitle_running_attempt_is_rerun_after_restart(self) -> None:
        # Owner, 2026-10-05: free work interrupted by a Hub restart runs again; it used to be fenced
        # `uncertain` for a person, holding its Mac.
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
                         ("queued", None, None, 1))
        self.assertFalse(restored.get("manual_action_required"))
        # On its last try a restart ends it as a known failure instead of a fence.
        restored.update(state="running", studio="subtitle", studio_task_id="stt-3", tries=3)
        transcription_jobs._save(transcription_jobs.batches[batch["id"]])
        transcription_jobs.batches.clear()
        transcription_jobs.restore_batches()
        restored = transcription_jobs.batches[batch["id"]]["items"][0]
        self.assertEqual((restored["state"], restored["error_code"]), ("error", "HUB_RESTARTED"))
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

                monitor = SimpleNamespace(_client=LostClient(), _transcribe_cache={})
                batch = batch_for("lost-subtitle-response")
                transcription_jobs.batches[batch["id"]] = batch
                await transcription_jobs._run_item(
                    monitor, batch, batch["items"][0], studio, "lost-owner")
                return batch

        with self.worker.lock:
            self.worker.transcription_posts = self.worker.transcription_completed = 0
        self.worker.transcription_started.clear()
        lost = asyncio.run(lost_response())
        # Owner decision 2026-10-03: a worker lost mid-transcription is re-run (on another Mac first)
        # instead of fencing this one; transcription is free and a second SRT is never read.
        self.assertEqual((lost["items"][0]["state"], lost["items"][0]["tries"]),
                         ("queued", 1))
        self.assertFalse(lost["items"][0].get("manual_action_required"))
        self.assertIn("local", lost["items"][0]["avoid_machines"])
        self.assertNotIn("local", broker.busy_machines())
        # Re-running is the dispatcher's job: this attempt itself posted exactly once.
        self.assertEqual((self.worker.transcription_posts, self.worker.transcription_completed), (1, 1))
        transcription_jobs.batches.clear()

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
        # The worker cannot be interrupted, so the Hub waits for the answer it is already producing:
        # the outcome is then known, the item is cancelled, and nothing is fenced.
        self.assertEqual([item["state"] for item in cancelled["items"]],
                         ["cancelled", "cancelled"])
        self.assertEqual([item["tries"] for item in cancelled["items"]], [1, 0])
        self.assertEqual(transcription_jobs.summary(cancelled)["status"], "cancelled")
        self.assertFalse(any(item.get("manual_action_required") for item in cancelled["items"]))
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
        # An item already fenced (a lost contact, not the restart: a restart re-runs running work)
        # keeps holding its Mac across a restart.
        machine = "physical-node-a"
        broker.configure_monitor(SimpleNamespace(registry=[
            {"id": "subtitle", "modality": "transcription", "machine": "renamed-node", "local": True},
        ]))
        input_path = _ROOT / "fenced-subtitle.wav"
        input_path.write_bytes(_AUDIO)
        batch = {"id": "fenced-subtitle", "idempotency_key": "fenced-subtitle-key",
                 "created_at": time.time(), "model": "fixture/subtitle", "cancelled": False,
                 "items": [{"index": 0, "item_id": "clip-1", "filename": "clip.wav",
                            "input_path": str(input_path), "state": "uncertain", "tries": 1,
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
                # A worker that timed out behind its gateway is a lost worker: re-run, not fenced
                # (owner decision 2026-10-03).
                with self.worker.lock:
                    self.worker.transcription_refusal = status
                item = asyncio.run(request(status))
                self.assertEqual(item["state"], "queued")
                self.assertFalse(item.get("manual_action_required"))
                self.assertNotIn("local", broker.busy_machines())
                transcription_jobs.batches.clear()
        finally:
            with self.worker.lock:
                self.worker.transcription_refusal = None
            transcription_jobs.batches.clear()

    # ── Rejected-section review candidates ────────────────────────────────────────────────────

    def _review_routes(self, batch_id: str) -> tuple[str, str]:
        base = f"/api/hub/jobs/{batch_id}/items/0/review-candidate"
        return base, base + "/audio"

    def test_a_refused_section_is_bound_on_its_final_attempt_and_outlives_its_batch(self) -> None:
        headers = {"Authorization": f"Bearer {_TOKEN}"}
        old_tries, old_delays = broker.MAX_TRIES, broker.RETRY_DELAYS_S
        broker.MAX_TRIES, broker.RETRY_DELAYS_S = 2, (.05,)
        try:
            app = standalone.create_app(start_background=True)
            with TestClient(app) as client:
                self._admit_fixture_catalog(app)
                batch_id = client.post("/api/hub/jobs", json={
                    "modality": "voice", "model": "fixture/voice", "clientRequestId": "refused-0001",
                    "items": [{"text": "refuse"}]}, headers=headers).json()["batch_id"]
                failed = self._wait_for(client, batch_id, "error", timeout=10)["items"][0]
                self.assertEqual(failed["error_code"], "TTS_SECTION_SILENT")
                self.assertTrue(failed["error"].startswith("SectionQualityError: Section 6 of 7"))
                self.assertEqual(client.get(f"/api/hub/jobs/{batch_id}/items/0/artifact",
                                            headers=headers).status_code, 404)
                attempts = [job_id for job_id, job in self.worker.jobs.items()
                            if job["params"].get("text") == "refuse"]
                self.assertEqual(len(attempts), 2)
                # It saw `error` while the receipt was still pending and asked again until it was.
                self.assertEqual(self.worker.review_reads.get(attempts[-1]), 3)
                self.assertNotIn(attempts[0], self.worker.review_reads)   # a retried try is never asked
                locator = ledger.review_locator(batch_id, 0)
                self.assertEqual(locator["worker_job_id"], attempts[-1])   # never the retried one
                self.assertEqual(locator["studio"], "voice")
                self.assertEqual((locator["pcm_bytes"], locator["pcm_sha256"]), (len(_AUDIO), _AUDIO_SHA))
                meta_route, audio_route = self._review_routes(batch_id)
                anonymous = TestClient(app)   # no session cookie from the calls above
                self.assertEqual(anonymous.get(meta_route).status_code, 401)
                self.assertEqual(anonymous.get(audio_route).status_code, 401)
                meta = client.get(meta_route, headers=headers)
                self.assertEqual(meta.status_code, 200, meta.text)
                body = meta.json()
                self.assertEqual({key: body[key] for key in ("active", "disposition", "deliverable", "imported")},
                                 {"active": False, "disposition": "review_only",
                                  "deliverable": False, "imported": False})
                self.assertEqual(body["receipt"], self.worker.review[attempts[-1]])
                self.assertEqual(body["audio_url"], audio_route)
                for private in ("handle.sig", _TOKEN, "127.0.0.1"):
                    self.assertNotIn(private, meta.text)
                self.assertEqual(meta.headers["cache-control"], "no-store, private, max-age=0")
                audio = client.get(audio_route, headers=headers)
                self.assertEqual((audio.status_code, audio.content), (200, _AUDIO))
                self.assertEqual(audio.headers["x-content-sha256"], _AUDIO_SHA)
                self.assertEqual(audio.headers["content-type"], "audio/wav")
                for method in ("post", "put", "patch", "delete"):
                    self.assertEqual(getattr(client, method)(audio_route, headers=headers).status_code, 405)

                broker.clear_finished_batches(batch_id=batch_id)                     # "Clear finished"
                ledger.compact_history(now=time.time() + 100 * 86400)                 # the 90-day trim
                self.assertEqual(client.get(f"/api/hub/jobs/{batch_id}", headers=headers).status_code, 404)

            app = standalone.create_app(start_background=False)                      # a restarted Hub
            with TestClient(app) as client:
                self.assertEqual(client.get(meta_route, headers=headers).status_code, 200)
                self.assertEqual(client.get(audio_route, headers=headers).content, _AUDIO)
                with self.worker.lock:
                    moved = self.worker.review.pop(attempts[-1])   # the owner moved it to _trash
                self.assertEqual(client.get(meta_route, headers=headers).status_code, 404)
                self.assertEqual(client.get(audio_route, headers=headers).status_code, 404)
                with self.worker.lock:
                    self.worker.review[attempts[-1]] = dict(moved, receipt_sha256="0" * 64)
                self.assertEqual(client.get(meta_route, headers=headers).status_code, 502)
                self.assertIsNotNone(ledger.review_locator(batch_id, 0))   # a GET never retires it
                self.assertEqual(client.get(self._review_routes("no-such-batch")[0],
                                            headers=headers).status_code, 404)
        finally:
            broker.MAX_TRIES, broker.RETRY_DELAYS_S = old_tries, old_delays
            # Each queue's wake event binds to the first loop that waits on it; the next test that
            # starts the dispatcher runs in a new TestClient loop, as a restarted Hub would.
            from components.hub.backend import chat_jobs
            for module in (broker, chat_jobs, transcription_jobs):
                module._wakeup = asyncio.Event()

    def test_a_node_held_candidate_is_read_only_through_the_nodes_granted_peer_routes(self) -> None:
        from urllib.parse import urlsplit
        from components.hub.backend import auth
        node = {"id": "voice@node-a", "modality": "voice", "host": "10.0.0.5", "hub_port": 8765,
                "machine": "node-a", "hub_token": "node-credential"}
        for path in ("/api/fleet/jobs/c85e8c5d0a1b/review-candidate",
                     "/api/fleet/jobs/c85e8c5d0a1b/review-candidate/media/eyJqIjoiYyJ9.c2ln"):
            url, sent = peers.studio_request(node, path)
            self.assertEqual(urlsplit(url).netloc, "10.0.0.5:8765")
            self.assertTrue(auth.fleet_path_allowed("GET", urlsplit(url).path), url)
            self.assertEqual(sent, {"X-Hub-Token": "node-credential"})

    def test_only_a_proven_terminal_voice_quality_failure_binds_a_candidate(self) -> None:
        studio = {"id": "voice", "modality": "voice", "host": "127.0.0.1", "port": self.worker.port,
                  "machine": "local", "local": True, "studio_token": _TOKEN, "hub_token": _TOKEN}

        held: dict = {}

        def attempt(job_id: str, receipt: dict | None, *, modality: str = "voice",
                    code: str = "TTS_SECTION_SILENT", final: dict | None = None,
                    pending: int = 0) -> dict:
            with self.worker.lock:
                self.worker.review.pop(job_id, None)
                if receipt is not None:
                    self.worker.review[job_id] = receipt
                self.worker.review_pending[job_id] = pending
                self.worker.review_reads[job_id] = 0
            batch = {"id": f"bind-{job_id}", "modality": modality, "model": "fixture/voice",
                     "cancelled": False, "shared_params": {}, "created_at": time.time(), "items": []}
            item = {"index": 0, "state": "running", "studio": "voice", "studio_job_id": job_id,
                    "client_request_id": f"studiohub:bind-{job_id}:0", "error_code": code,
                    "error": _REFUSAL,
                    "section_quality": {"revision": "voicestudio.tts-section-quality.v3",
                                        "sections": [dict(final or _FINAL_ROW)]}}
            batch["items"].append(item)

            async def run() -> None:
                async with httpx.AsyncClient() as client:
                    await broker._attach_review_candidate(client, batch, item, studio, code)

            asyncio.run(run())
            self.assertEqual((item["error_code"], item["error"]), (code, _REFUSAL))   # untouched
            item["state"] = "error"
            held.update(batch=batch, item=item)
            # The locator waits on the item for its terminal save: never in any public view.
            self.assertNotIn("_review_candidate", broker.public_item(batch, item))
            broker.batches[batch["id"]] = batch
            with TestClient(app) as client:
                view = client.get(f"/api/hub/jobs/{batch['id']}", headers={"Authorization": f"Bearer {_TOKEN}"})
            self.assertEqual(view.status_code, 200, view.text)
            self.assertNotIn("_review_candidate", view.text)
            self.assertNotIn(_AUDIO_SHA, view.text)
            ledger.save_batch(batch)
            broker.batches.pop(batch["id"], None)
            return ledger.review_locator(batch["id"], 0)

        app = standalone.create_app(start_background=False)
        good = "b00000000001"
        self.assertIsNotNone(attempt(good, _review_receipt(good, f"studiohub:bind-{good}:0")))
        late = "b0000000000c"   # error first, receipt two polls later: the real worker's order
        self.assertIsNotNone(attempt(late, _review_receipt(late, f"studiohub:bind-{late}:0"), pending=2))
        self.assertEqual(self.worker.review_reads[late], 3)
        old_wait, old_poll = broker.REVIEW_PENDING_WAIT_S, broker.REVIEW_PENDING_POLL_S
        broker.REVIEW_PENDING_WAIT_S, broker.REVIEW_PENDING_POLL_S = .3, .05
        try:
            never = "b0000000000d"   # a receipt that never becomes durable: bounded, then nothing
            started = time.monotonic()
            self.assertIsNone(attempt(never, _review_receipt(never, f"studiohub:bind-{never}:0"), pending=-1))
            self.assertLess(time.monotonic() - started, 5)
            self.assertGreater(self.worker.review_reads[never], 1)
        finally:
            broker.REVIEW_PENDING_WAIT_S, broker.REVIEW_PENDING_POLL_S = old_wait, old_poll
        self.assertIsNone(attempt("b0000000000e", None))   # 404: no candidate, asked once
        self.assertEqual(self.worker.review_reads["b0000000000e"], 1)
        for name, job_id, receipt, kwargs in (
            ("missing", "b00000000002", None, {}),
            ("other worker job", "b00000000003", _review_receipt("b0000000000f", "studiohub:bind-b00000000003:0"), {}),
            ("other request", "b00000000004", _review_receipt("b00000000004", "studiohub:other:0"), {}),
            ("tampered", "b00000000005", dict(_review_receipt("b00000000005", "studiohub:bind-b00000000005:0"), active=True), {}),
            ("active", "b00000000006", _review_receipt("b00000000006", "studiohub:bind-b00000000006:0", active=True), {}),
            ("other final row", "b00000000007", _review_receipt("b00000000007", "studiohub:bind-b00000000007:0"),
             {"final": dict(_FINAL_ROW, voiced_s=2.6)}),
            ("not voice", "b00000000008", _review_receipt("b00000000008", "studiohub:bind-b00000000008:0"), {"modality": "image"}),
            ("not a quality failure", "b00000000009", _review_receipt("b00000000009", "studiohub:bind-b00000000009:0"),
             {"code": "WORKER_TERMINAL_ERROR"}),
            ("other rejection", "b0000000000a", _review_receipt("b0000000000a", "studiohub:bind-b0000000000a:0"),
             {"code": "TTS_SECTION_TOO_LONG"}),
            ("oversized pcm", "b0000000000b", _review_receipt("b0000000000b", "studiohub:bind-b0000000000b:0",
             audio={"media_type": "audio/wav", "bytes": broker.REVIEW_MAX_PCM_BYTES + 1, "sha256": _AUDIO_SHA}), {}),
        ):
            with self.subTest(name):
                self.assertIsNone(attempt(job_id, receipt, **kwargs))

    def test_the_hub_proves_every_byte_of_the_candidate_before_the_caller_has_it(self) -> None:
        from unittest.mock import patch
        headers = {"Authorization": f"Bearer {_TOKEN}"}
        job_id = "c00000000001"
        with self.worker.lock:
            self.worker.review[job_id] = _review_receipt(job_id, "studiohub:body-proof:0")
        ledger.save_batch({"id": "body-proof", "created_at": time.time(), "items": [{
            "index": 0, "state": "error", "_review_candidate": {
                "studio": "voice", "worker_job_id": job_id, "client_request_id": "studiohub:body-proof:0",
                "final_quality_sha256": broker.review_sha256(_FINAL_ROW),
                "receipt_sha256": self.worker.review[job_id]["receipt_sha256"],
                "pcm_bytes": len(_AUDIO), "pcm_sha256": _AUDIO_SHA}}]})
        _meta, audio_route = self._review_routes("body-proof")
        real_send = httpx.AsyncClient.send
        streams: list = []
        plan: dict = {}

        class Stream(httpx.AsyncByteStream):
            def __init__(self, chunks, fail):
                self.chunks, self.fail, self.closed = chunks, fail, 0

            async def __aiter__(self):
                for chunk in self.chunks:
                    yield chunk
                if self.fail:
                    raise httpx.ReadError("the worker dropped the connection")

            async def aclose(self):
                self.closed += 1

        async def send(client, request, **kwargs):
            if "/review-candidate/media/" not in request.url.path or not plan:
                return await real_send(client, request, **kwargs)
            stream = Stream(plan["chunks"], plan.get("fail", False))
            streams.append(stream)
            raw = [(b"content-type", b"audio/wav"), (b"content-length", str(len(_AUDIO)).encode()),
                   (b"x-content-sha256", _AUDIO_SHA.encode())]
            raw = [row for row in raw if row[0] not in plan.get("drop", ())] + plan.get("add", [])
            raw = [(name, plan.get("set", {}).get(name, value)) for name, value in raw]
            return httpx.Response(200, headers=raw, stream=stream, request=request)

        exact = [_AUDIO[:10], _AUDIO[10:]]
        flipped = _AUDIO[:-1] + bytes([_AUDIO[-1] ^ 1])
        cases = {
            "exact": {"chunks": exact},
            "one byte over": {"chunks": [_AUDIO, b"x"]},
            "chunk crosses the end": {"chunks": [_AUDIO[:10], _AUDIO[10:] + b"x"]},
            "early end": {"chunks": [_AUDIO[:-1]]},
            "same length, other bytes": {"chunks": [flipped[:10], flipped[10:]]},
            "worker drops mid-body": {"chunks": [_AUDIO[:10]], "fail": True},
            "no length": {"chunks": exact, "drop": (b"content-length",)},
            "two digests": {"chunks": exact, "add": [(b"x-content-sha256", _AUDIO_SHA.encode())]},
            "malformed length": {"chunks": exact, "set": {b"content-length": b"+%d" % len(_AUDIO)}},
            "other length": {"chunks": exact, "set": {b"content-length": b"%d" % (len(_AUDIO) + 1)}},
            "other type": {"chunks": exact, "set": {b"content-type": b"audio/mpeg"}},
            "other digest": {"chunks": exact, "set": {b"x-content-sha256": b"0" * 64}},
            "uppercase digest": {"chunks": exact, "set": {b"x-content-sha256": _AUDIO_SHA.upper().encode()}},
        }
        app = standalone.create_app(start_background=False)
        with patch.object(httpx.AsyncClient, "send", send), TestClient(app) as client:
            for name, case in cases.items():
                with self.subTest(name):
                    plan.clear()
                    plan.update(case)
                    try:
                        response = client.get(audio_route, headers=headers)
                        received = (response.status_code, response.content)
                    except Exception:   # the Hub aborted the body part-way
                        received = None
                    self.assertEqual(received == (200, _AUDIO), name == "exact", received and received[0])
                    self.assertGreaterEqual(streams[-1].closed, 1)

            # A caller that goes away after the first bytes still releases the worker stream.
            plan.clear()
            plan.update(chunks=[_AUDIO[:10], _AUDIO[10:20], _AUDIO[20:]])
            endpoint = next(route.endpoint for route in app.routes
                            if getattr(route, "path", "") == "/api/hub/jobs/{batch_id}/items/{item_index}/review-candidate/audio")

            async def walk_away() -> bytes:
                response = await endpoint("body-proof", 0)
                first = await response.body_iterator.__anext__()
                await response.body_iterator.aclose()
                return first

            self.assertEqual(asyncio.run(walk_away()), _AUDIO[:10])
            self.assertEqual(streams[-1].closed, 1)


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


class VoiceChoiceLiftingTests(unittest.TestCase):
    """Both ways of naming a voice must survive submission.

    The Hub lifts a voice choice from the top level of the envelope into `sharedParams`, which is
    where dispatch reads it. `voice_reference_asset_id` was lifted from the start;
    `voice_library_id` was not, so a saved voice chosen in the app was dropped on the way to the
    Voice worker - which then refused the job with "Voice cloning needs a reference voice or private
    reference upload", a message about a reference the operator had not been asked for. These pin
    both keys, and pin that an explicit `sharedParams` value still wins.
    """

    def _lift(self, envelope):
        from components.hub.backend import broker
        return broker._shared_params_with_local_reference(envelope)

    def test_a_top_level_saved_voice_is_lifted(self):
        self.assertEqual(self._lift({"voice_library_id": " b96e838f75ab "}),
                         {"voice_library_id": "b96e838f75ab"})

    def test_a_top_level_reference_asset_is_still_lifted(self):
        self.assertEqual(self._lift({"voice_reference_asset_id": "abc123"}),
                         {"voice_reference_asset_id": "abc123"})

    def test_both_choices_travel_together(self):
        lifted = self._lift({"voice_library_id": "voice-1", "voice_reference_asset_id": "asset-1"})
        self.assertEqual(lifted, {"voice_library_id": "voice-1",
                                  "voice_reference_asset_id": "asset-1"})

    def test_an_explicit_shared_param_is_not_overwritten(self):
        lifted = self._lift({"voice_library_id": "outer",
                             "sharedParams": {"voice_library_id": "inner"}})
        self.assertEqual(lifted["voice_library_id"], "inner")

    def test_an_absent_choice_adds_nothing(self):
        self.assertEqual(self._lift({}), {})
        self.assertEqual(self._lift({"voice_library_id": "   "}), {})



class FinishedBatchMemoryTests(unittest.TestCase):
    """A Hub that runs for weeks must not hold every job it has ever seen.

    `broker.batches` is the in-memory copy that a running dispatch is polled through; the durable
    record is the ledger, and the listings merge the two. Nothing ever removed a finished batch, so
    the dict - and every walk of it, on every status read - grew without bound.
    """

    def test_old_finished_batches_leave_memory_but_recent_and_running_ones_stay(self):
        import time as _time
        from components.hub.backend import broker
        saved = dict(broker.batches)
        try:
            broker.batches.clear()
            now = _time.time()
            broker.batches.update({
                "long-done": {"id": "long-done", "modality": "voice", "created_at": now - 7200,
                              "finished_at": now - 7200, "items": [{"state": "done"}]},
                "just-finished": {"id": "just-finished", "modality": "voice", "created_at": now,
                                  "finished_at": now, "items": [{"state": "done"}]},
                "still-running": {"id": "still-running", "modality": "voice", "created_at": now - 9000,
                                  "items": [{"state": "running"}]},
                "half-done": {"id": "half-done", "modality": "voice", "created_at": now - 9000,
                              "finished_at": now - 9000,
                              "items": [{"state": "done"}, {"state": "queued"}]},
            })
            removed = broker.evict_finished_batches(keep_recent=1, older_than_s=900)
            self.assertEqual(removed, 1)
            self.assertNotIn("long-done", broker.batches, "an old finished batch is pure memory")
            self.assertIn("just-finished", broker.batches, "the recent ones are the ETA basis")
            self.assertIn("still-running", broker.batches, "a running job is not history")
            self.assertIn("half-done", broker.batches, "half a batch is not finished")
        finally:
            broker.batches.clear()
            broker.batches.update(saved)


class EveryJobLaneIsListedOrDeclaredTests(unittest.TestCase):
    """`GET /api/hub/jobs` must list a lane's jobs, or say in one place why it does not.

    A Chat-page completion used to leave no job at all, because the listing read the ledger's
    finished batches and a completion was never a batch. The same hole is one module away for the
    next studio, so this walks the backend's own queue modules: a `*_jobs.py` that owns a queue is
    either part of `broker.listed_batches()` or named in `broker.UNLISTED_JOB_LANES` with its
    reason - and the declaration itself is checked, so a stale one fails too.
    """

    SENTINEL = {
        "id": "sentinel-batch", "modality": "image", "model": "sentinel-model",
        "created_at": time.time(), "cancelled": False,
        "items": [{"index": 0, "state": "done", "runtime_s": 1.0,
                   "finished_at": time.time()}],
    }

    @staticmethod
    def queue_modules() -> list[str]:
        """Backend modules that own a module-level `batches` queue, found without importing them."""
        backend = Path(__file__).resolve().parents[1] / "components" / "hub" / "backend"
        found = []
        for path in sorted(backend.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                targets = node.targets if isinstance(node, ast.Assign) else (
                    [node.target] if isinstance(node, ast.AnnAssign) else [])
                if any(isinstance(target, ast.Name) and target.id == "batches"
                       for target in targets):
                    found.append(path.stem)
                    break
        return found

    def test_every_queue_module_reaches_the_listing_or_declares_why_not(self) -> None:
        from components.hub.backend import broker, ledger

        names = self.queue_modules()
        self.assertIn("broker", names, "the scan found no queues at all")
        with tempfile.TemporaryDirectory(prefix="studiohub-listed-") as state:
            with patch.object(ledger, "DB_FILE", Path(state) / "hub.db"):
                declared = broker.UNLISTED_JOB_LANES
                for name in names:
                    module = importlib.import_module(f"components.hub.backend.{name}")
                    module.batches["sentinel-batch"] = dict(self.SENTINEL)
                    try:
                        listed = {row["id"] for row in broker.listed_batches()}
                    finally:
                        module.batches.pop("sentinel-batch", None)
                    if name in declared:
                        self.assertTrue(declared[name].strip(),
                                        f"{name} is declared unlisted without a reason")
                        self.assertNotIn(
                            "sentinel-batch", listed,
                            f"{name} is declared unlisted but its jobs reach /api/hub/jobs; "
                            f"remove the declaration")
                    else:
                        self.assertIn(
                            "sentinel-batch", listed,
                            f"{name} owns a job queue that /api/hub/jobs can never show; list it "
                            f"in broker.listed_batches() or declare it in broker.UNLISTED_JOB_LANES")

    def test_a_chat_page_completion_is_summarisable_through_the_listing(self) -> None:
        from components.hub.backend import broker, chat_jobs, ledger

        with tempfile.TemporaryDirectory(prefix="studiohub-chat-listed-") as state:
            with patch.object(ledger, "DB_FILE", Path(state) / "hub.db"), \
                    patch.object(broker, "batches", {}):
                done = chat_jobs.record_page_completion(
                    request_body=b'{"model": "mlx-community/Qwen3.5-9B-4bit", "messages": '
                                 b'[{"role": "user", "content": "when is low tide?"}]}',
                    started_at=time.time() - 2, runtime_s=2.0, worker_id="chat",
                    machine="local", status_code=200, content_type="application/json",
                    content=b'{"id": "chatcmpl-1", "model": "mlx-community/Qwen3.5-9B-4bit",'
                            b' "choices": [{"message": {"role": "assistant",'
                            b' "content": "Low water is at 04:12."}}]}')
                failed = chat_jobs.record_page_completion(
                    request_body=b'{"model": "mlx-community/Qwen3.5-9B-4bit"}',
                    started_at=time.time() - 1, runtime_s=1.0, worker_id="chat",
                    machine="local", status_code=409,
                    content_type="application/json",
                    content=b'{"detail": "the model is not installed"}')
                rows = broker.listed_batches()
        self.assertEqual({row["id"] for row in rows}, {done["id"], failed["id"]})
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id[done["id"]]["modality"], "chat")
        self.assertEqual(by_id[done["id"]]["model"], "mlx-community/Qwen3.5-9B-4bit")
        self.assertEqual((by_id[done["id"]]["done"], by_id[done["id"]]["error"]), (1, 0))
        self.assertEqual((by_id[failed["id"]]["done"], by_id[failed["id"]]["error"]), (0, 1))
        self.assertEqual(by_id[failed["id"]]["errors"][0]["message"], "the model is not installed")
        self.assertEqual(done["items"][0]["text"], "Low water is at 04:12.")
        self.assertIsNone(failed["items"][0]["text"])
        self.assertEqual(failed["items"][0]["state"], "error")
        # The answer is the artifact the generic job route serves, not a private field.
        self.assertEqual(done["items"][0]["media_type"], "text/plain; charset=utf-8")
        self.assertGreater(done["items"][0]["bytes"], 0)


if __name__ == "__main__":
    unittest.main()


class NodeLocalGenerationTests(unittest.TestCase):
    """A Mac switched to a node must still run work for the person sitting at it.

    This class is deliberately **outside** the `_PROOF` string above. That string is a complete
    packaged-Hub program executed in a subprocess by `test_real_broker_lifecycle_proof`, and a test
    written inside it is source for that subprocess rather than a test of this suite — which is how
    a previous session concluded ten live tests were dead, and how this one nearly did the same
    again by counting `class` lines with grep instead of asking the parser.
    """

    def test_a_node_still_runs_work_for_the_person_sitting_at_this_mac(self) -> None:
        """A Mac switched to a node could not generate anything from its own window.

        ``accepts_customer_jobs()`` refused *every* submission in agent mode with a 409 — on the
        reasonable grounds that an agent must not own a **customer's** job. But the owner's own
        app on his own Mac is not a customer, and the gate could not tell the two apart. Measured
        by the owner: *"when any of the machine are in node mode. i can't generate on that machine
        manually. bad. i want to be able to."*

        The difference is the credential and nothing else: the Hub token is this Mac's own, the
        fleet token is someone else's. The fleet refusal is asserted here too, because narrowing a
        gate is only safe if the thing it was protecting still holds.
        """
        from unittest import mock
        from fastapi.testclient import TestClient
        from components.hub import standalone
        from components.hub.backend import auth, control_plane, peers

        app = standalone.create_app(start_background=False)
        # The app's own credential, read the way the app reads it: `_TOKEN` is a name inside the
        # `_PROOF` string above and does not exist out here.
        owner = {"Authorization": f"Bearer {auth.load_token()}"}
        fleet = {"Authorization": "Bearer fixture-fleet-token"}
        with mock.patch.object(control_plane, "load_settings", return_value={"role": "agent"}), \
                mock.patch.object(peers, "fleet_token", return_value="fixture-fleet-token"), \
                TestClient(app) as client:
            # The owner's own app: refused for whatever else is wrong with an empty envelope, but
            # never for being in agent mode.
            mine = client.post("/api/hub/jobs", json={}, headers=owner)
            self.assertNotEqual(mine.status_code, 409, mine.text)
            self.assertNotIn("agent mode", mine.text)

            # A controller: still refused, which is the rule the gate exists for.
            theirs = client.post("/api/hub/jobs", json={}, headers=fleet)
            self.assertEqual(theirs.status_code, 409, theirs.text)
            self.assertIn("agent mode", theirs.text)

