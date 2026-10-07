"""Fake-only checks for Hub Chat telemetry and targeted activity forwarding."""
from __future__ import annotations

import asyncio
import json
import math
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from components.hub.backend import activity, chat_jobs, ledger, monitor
from tests.test_chat_hub_wiring import MODEL, _PackagedHub, _StubWorker


TELEMETRY = {
    "attempts": [
        {"attempt": 1, "prompt_tokens": 12, "prompt_tps": 120.5,
         "generation_tokens": 2, "generation_tps": 20.0,
         "peak_memory_bytes": 1_400_000_000, "finish_reason": "stop"},
        {"attempt": 2, "prompt_tokens": 18, "prompt_tps": 90.0,
         "generation_tokens": 4, "generation_tps": 18.5,
         "peak_memory_bytes": 1_500_000_000, "finish_reason": "stop"},
    ],
    "final_attempt": 2,
}


def _buffered(telemetry=TELEMETRY) -> bytes:
    body = {
        "id": "chatcmpl-metrics", "model": MODEL,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "answer"}}],
        "usage": {"prompt_tokens": 18, "completion_tokens": 4, "total_tokens": 22},
        "studio_hub": {"elapsed_seconds": 0.25},
    }
    if telemetry is not None:
        body["studio_hub"]["telemetry"] = telemetry
    return json.dumps(body).encode()


def _streamed(telemetry=TELEMETRY) -> bytes:
    final = {
        "id": "chatcmpl-metrics", "choices": [{"delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 18, "completion_tokens": 4, "total_tokens": 22},
        "studio_hub": {"elapsed_seconds": 0.25},
    }
    if telemetry is not None:
        final["studio_hub"]["telemetry"] = telemetry
    return (b'data: {"id":"chatcmpl-metrics","choices":[{"delta":{"content":"answer"}}]}\n\n'
            + b"data: " + json.dumps(final).encode() + b"\n\ndata: [DONE]\n\n")


class ChatActivityMetricBoundaryTests(unittest.TestCase):
    def test_worker_metrics_cross_the_monitor_and_activity_closed_allowlists(self) -> None:
        now = time.time()
        raw = {
            "schema": activity.SCHEMA, "studio": "chat", "observed_at": now,
            "active": {
                "id": "job-1", "state": "running", "model": MODEL,
                "source": "direct", "origin": "unknown",
                "created_at": now - 1, "started_at": now - 1, "updated_at": now,
                "progress_stage": "processing", "attempt": 2,
                "processed_prompt_tokens": 9, "total_prompt_tokens": 18,
                "prompt_tokens": 18, "prompt_tps": 90.0,
                "generation_tokens": 4, "generation_tps": 18.5,
                "peak_memory_bytes": 1_500_000_000, "finish_reason": "stop",
                "prompt": "private prompt", "telemetry": TELEMETRY,
            },
            "latest": None, "loaded": True, "credentials": {"token": "secret"},
        }
        validated = activity.validate_snapshot(raw, expected_studio="chat")
        self.assertIsNotNone(validated)
        job = validated["active"]
        self.assertEqual(job["progress_stage"], "processing")
        self.assertEqual(job["processed_prompt_tokens"], 9)
        self.assertEqual(job["total_prompt_tokens"], 18)
        self.assertEqual(job["generation_tps"], 18.5)
        self.assertEqual(job["peak_memory_bytes"], 1_500_000_000)
        self.assertNotIn("prompt", job)
        self.assertNotIn("telemetry", job)
        handed_off = monitor._activity_snapshot_view(validated)
        self.assertEqual(handed_off["active"]["progress_stage"], "processing")
        self.assertEqual(handed_off["active"]["attempt"], 2)
        self.assertNotIn("loaded", handed_off)
        self.assertNotIn("credentials", handed_off)

    def test_missing_nan_bool_and_object_metrics_stay_absent(self) -> None:
        now = time.time()
        raw = {
            "schema": activity.SCHEMA, "studio": "chat", "observed_at": now,
            "active": {
                "id": "job-1", "state": "running", "model": MODEL,
                "source": "direct", "origin": "unknown",
                "created_at": now - 1, "started_at": now - 1, "updated_at": now,
                "progress_stage": "not-a-phase", "attempt": True,
                "processed_prompt_tokens": -1, "total_prompt_tokens": {},
                "prompt_tokens": False, "prompt_tps": math.nan,
                "generation_tokens": 1.5, "generation_tps": math.inf,
                "peak_memory_bytes": [], "finish_reason": {"text": "stop"},
            },
            "latest": None,
        }
        job = activity.validate_snapshot(raw, expected_studio="chat")["active"]
        for key in (
            "progress_stage", "attempt", "processed_prompt_tokens", "total_prompt_tokens",
            "prompt_tokens", "prompt_tps", "generation_tokens", "generation_tps",
            "peak_memory_bytes", "finish_reason",
        ):
            self.assertNotIn(key, job)


class ChatCompletionTelemetryPersistenceTests(unittest.TestCase):
    def _record(self, content: bytes, content_type: str) -> dict:
        with tempfile.TemporaryDirectory(prefix="studiohub-chat-metrics-") as state:
            with patch.object(ledger, "DB_FILE", Path(state) / "hub.db"):
                saved = chat_jobs.record_page_completion(
                    request_body=json.dumps({"model": MODEL, "messages": [
                        {"role": "user", "content": "private prompt"}
                    ]}).encode(),
                    started_at=time.time() - 1, runtime_s=1.0, worker_id="chat",
                    machine="local", status_code=200, content_type=content_type,
                    content=content,
                )
                restored = ledger.load_batch(saved["id"])
        return restored["items"][0]

    def test_buffered_completion_persists_distinct_attempts(self) -> None:
        raw = json.loads(json.dumps(TELEMETRY))
        raw["prompt"] = "private prompt"
        raw["attempts"][0]["messages"] = [{"content": "private prompt"}]
        item = self._record(_buffered(raw), "application/json")
        self.assertEqual(item["chat_telemetry"], TELEMETRY)
        self.assertEqual([row["generation_tps"] for row in item["chat_telemetry"]["attempts"]],
                         [20.0, 18.5])
        self.assertEqual(item["usage"]["completion_tokens"], 4)
        self.assertEqual(item["generation_s"], 0.25)
        self.assertEqual(len(chat_jobs.completion_outcome(
            status_code=200, content_type="application/json", content=_buffered())), 7)

    def test_sse_final_chunk_persists_distinct_attempts(self) -> None:
        item = self._record(_streamed(), "text/event-stream")
        self.assertEqual(item["chat_telemetry"], TELEMETRY)
        self.assertEqual(item["chat_telemetry"]["final_attempt"], 2)
        self.assertEqual(item["text"], "answer")

    def test_malformed_telemetry_is_absent_without_changing_legacy_facts(self) -> None:
        malformed = {
            "attempts": [{"attempt": True, "prompt_tps": math.nan,
                          "generation_tokens": False, "prompt": "private"}],
            "final_attempt": True,
        }
        item = self._record(_buffered(malformed), "application/json")
        self.assertIsNone(item["chat_telemetry"])
        self.assertEqual(item["usage"]["completion_tokens"], 4)
        self.assertEqual(item["generation_s"], 0.25)

        bool_attempt = {"attempts": [{"attempt": True}], "final_attempt": 1}
        self.assertIsNone(self._record(
            _buffered(bool_attempt), "application/json")["chat_telemetry"])

        optional = {"attempts": [{"attempt": 1, "prompt_tps": math.nan,
                                  "generation_tokens": False, "finish_reason": {}}],
                    "final_attempt": 1}
        item = self._record(_buffered(optional), "application/json")
        self.assertEqual(item["chat_telemetry"],
                         {"attempts": [{"attempt": 1}], "final_attempt": 1})

    def test_dispatched_pack_persists_and_summarises_worker_telemetry(self) -> None:
        class Response:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return json.loads(_buffered())

        class Client:
            async def post(self, *args, **kwargs):
                return Response()

        class Monitor:
            _client = Client()

        batch = {"id": "batch-1", "kind": "completion", "model": MODEL,
                 "created_at": time.time() - 1,
                 "cancelled": False, "packs": [], "genstudio_execution": None}
        pack = {"index": 0, "pack_id": "pack-1", "scene_ids": ["answer"],
                "messages": [{"role": "user", "content": "private prompt"}], "params": {},
                "state": "running", "tries": 1, "studio": "chat", "results": {},
                "raw_responses": [], "usage": None}
        batch["packs"] = [pack]
        studio = {"id": "chat", "machine": "local", "host": "127.0.0.1", "port": 1,
                  "hub_token": "worker-token", "modality": "chat"}
        with patch.object(chat_jobs, "_save"):
            asyncio.run(chat_jobs._run_pack(Monitor(), batch, pack, studio, "owner"))
        self.assertEqual(pack.get("chat_telemetry"), TELEMETRY, pack)
        self.assertEqual(chat_jobs.summary(batch)["packs"][0]["chat_telemetry"], TELEMETRY)


class ChatActivityProxyTests(_PackagedHub, unittest.TestCase):
    TOKEN = "chat-activity-route-token"
    WORKER_TOKEN = "chat-activity-worker-token"

    def setUp(self) -> None:
        self.job_id = str(uuid.uuid4())
        now = time.time()
        self.snapshot = {
            "schema": activity.SCHEMA, "studio": "chat", "observed_at": now,
            "active": {
                "id": self.job_id, "state": "running", "model": MODEL,
                "source": "direct", "origin": "unknown",
                "created_at": now - 1, "started_at": now - 1, "updated_at": now,
                "progress_stage": "generating", "attempt": 1,
                "generation_tokens": 4, "generation_tps": 18.5,
                "prompt": "private prompt",
            },
            "latest": None, "loaded": True,
        }
        self.stub = _StubWorker(routes={
            f"/api/fleet/activity?job_id={self.job_id}": self.snapshot,
        })
        self.addCleanup(self.stub.close)
        self.build_hub([{
            "id": "chat", "title": "Chat Studio", "modality": "chat",
            "host": "127.0.0.1", "port": self.stub.port, "machine": "node-chat",
            "local": True, "runtime_identity": {"id": "chat-runtime"},
            "hub_token": self.WORKER_TOKEN, "managed_output_roots": [],
        }])
        self.addCleanup(self.tear_down_hub)

    def test_owner_get_proxies_one_uuid_and_returns_only_the_correlated_snapshot(self) -> None:
        fleet_token = "chat-activity-fleet-token-0123456789"
        self.use_fleet_credential(fleet_token)
        with TestClient(self.app) as client:
            denied = client.get(f"/api/hub/chat/activity?job_id={self.job_id}")
            fleet_denied = client.get(
                f"/api/hub/chat/activity?job_id={self.job_id}",
                headers=self.fleet_headers_for(fleet_token))
            response = client.get(
                f"/api/hub/chat/activity?job_id={self.job_id}", headers=self.owner_headers())
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(fleet_denied.status_code, 403)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["active"]["id"], self.job_id)
        self.assertEqual(response.json()["active"]["progress_stage"], "generating")
        self.assertNotIn("prompt", response.json()["active"])
        self.assertNotIn("loaded", response.json())
        sent = self.stub.requests[-1]
        self.assertEqual(sent["path"], f"/api/fleet/activity?job_id={self.job_id}")
        self.assertEqual(sent["method"], "GET")
        self.assertEqual(sent["body"], b"")
        self.assertEqual(sent["token"], self.WORKER_TOKEN)

    def test_invalid_uuid_is_refused_before_the_worker_and_wrong_job_is_rejected(self) -> None:
        with TestClient(self.app) as client:
            invalid = client.get("/api/hub/chat/activity?job_id=not-a-uuid",
                                 headers=self.owner_headers())
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.stub.requests, [])

        other = str(uuid.uuid4())
        wrong = dict(self.snapshot)
        wrong["active"] = {**self.snapshot["active"], "id": other}
        self.stub.routes[f"/api/fleet/activity?job_id={self.job_id}"] = wrong
        with TestClient(self.app) as client:
            response = client.get(
                f"/api/hub/chat/activity?job_id={self.job_id}", headers=self.owner_headers())
        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
