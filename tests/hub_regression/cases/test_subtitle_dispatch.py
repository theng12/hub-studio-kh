"""The Hub transcription lifecycle can drive the independent Subtitle worker.

These cases use the packaged worker manifest fixture, which already registers a
``subtitle`` worker with ``modality == "transcription"``. They prove the Hub's
existing dispatcher selects that worker and sends exactly the request the new
``components/subtitle`` worker accepts. They do not run the real worker; the
end-to-end worker proof lives in ``tests/test_subtitle_bundle.py``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import time

import httpx
import pytest

from components.hub.backend import broker, transcription_jobs


class WorkerClient:
    """Records the single multipart POST the Hub makes to a licensed worker."""

    def __init__(self, payload):
        self.payload = payload
        self.posts: list[dict] = []

    async def post(self, url, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return httpx.Response(200, json=self.payload,
                              request=httpx.Request("POST", url))


def response(status, payload):
    return httpx.Response(status, json=payload,
                          request=httpx.Request("GET", "http://127.0.0.1/"))


def _subtitle_batch(tmp_path: Path) -> dict:
    """A Hub-owned batch directory laid out the way the dispatcher expects."""
    batch_id = "subtitle-batch"
    audio = transcription_jobs.ROOT / batch_id / "input" / "0000-abcd.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    (transcription_jobs.ROOT / batch_id / "output").mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"RIFF....WAVEfmt ")
    batch = {"id": batch_id, "idempotency_key": "k", "created_at": time.time(),
             "updated_at": time.time(), "finished_at": None, "cancelled": False,
             "model": "mlx-community/whisper-large-v3-turbo", "language": "en",
             "word_timestamps": False, "items": []}
    item = {"index": 0, "item_id": "chapter-1", "filename": "chapter-1.wav",
            "input_path": str(audio), "bytes": 16, "state": "queued", "tries": 0,
            "studio": None, "studio_task_id": None, "duration_seconds": None,
            "media_duration_seconds": None, "artifact_path": None, "error": None,
            "metadata": None, "avoid_machines": {}, "attempt_history": []}
    batch["items"] = [item]
    transcription_jobs.batches[batch["id"]] = batch
    return batch


def _up_monitor(fixture_monitor):
    studio = next(row for row in fixture_monitor.registry if row["id"] == "subtitle")
    fixture_monitor.status[studio["id"]] = {"status": "up", "last_seen": time.time(),
                                            "last_checked": time.time()}
    fixture_monitor._transcribe_cache[studio["id"]] = (time.time(), {
        "available": True,
        "models": [{"repo": "mlx-community/whisper-large-v3-turbo", "cached": True}],
    })
    return studio


def test_subtitle_worker_is_eligible_for_transcription_jobs(fixture_monitor):
    studio = _up_monitor(fixture_monitor)
    eligible = asyncio.run(transcription_jobs._eligible_studios(
        fixture_monitor, "mlx-community/whisper-large-v3-turbo"))
    assert [row["id"] for row in eligible] == [studio["id"]]
    assert studio["modality"] == "transcription"


def test_hub_dispatch_posts_the_subtitle_worker_request_shape(
        fixture_monitor, tmp_path):
    studio = _up_monitor(fixture_monitor)
    batch = _subtitle_batch(tmp_path)
    client = WorkerClient({
        "text": "hello world", "language": "en", "duration": 1.0,
        "srt": "1\n00:00:00,000 --> 00:00:01,000\nhello world\n",
        "vtt": "WEBVTT\n", "elapsed_seconds": 0.5,
        "studio_task_id": "stt-subtitle-batch-0",
        "model_revision": "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb",
    })
    fixture_monitor._client = client
    item = batch["items"][0]
    item["studio_task_id"] = "stt-subtitle-batch-0"
    asyncio.run(transcription_jobs._run_item(
        fixture_monitor, batch, item, studio, "transcription:subtitle:0"))
    assert len(client.posts) == 1, client.posts
    post = client.posts[0]
    assert post["url"].endswith("/api/transcribe")
    assert post["url"].startswith(studio["base_url"])
    # The local private worker is authenticated with the shared owner token.
    token = post["headers"].get("X-Studio-Token") or post["headers"].get("X-Hub-Token")
    assert token == studio["hub_token"]
    # Exactly the multipart fields the independent Subtitle worker accepts.
    assert post["data"] == {
        "model": "mlx-community/whisper-large-v3-turbo",
        "word_timestamps": "false",
        "activity_id": "stt-subtitle-batch-0",
        "language": "en",
    }
    assert set(post["files"]) == {"file"}
    assert item["state"] == "done"
    assert Path(item["artifact_path"]).read_text().startswith("1\n00:00:00,000")


def test_subtitle_failure_keeps_the_attempt_and_error_reported(
        fixture_monitor, tmp_path):
    """A delivered worker refusal stays explicit; only ambiguity is fenced."""
    studio = _up_monitor(fixture_monitor)

    class FailingClient:
        calls = 0

        async def post(self, url, **kwargs):
            self.calls += 1
            return response(409, {"detail": "safetensors required"})

    client = FailingClient()
    fixture_monitor._client = client
    batch = _subtitle_batch(tmp_path)
    item = batch["items"][0]
    # A delivered refusal is a known outcome: retryable, never fenced.
    item["tries"] = 1
    asyncio.run(transcription_jobs._run_item(
        fixture_monitor, batch, item, studio, "transcription:subtitle:0"))
    assert item["state"] == "queued"
    assert item["studio"] is None
    assert item["tries"] == 1
    assert "safetensors required" in item["error"]

    # Once attempts are exhausted the same refusal is reported as an error.
    item["tries"] = transcription_jobs.MAX_TRIES
    asyncio.run(transcription_jobs._run_item(
        fixture_monitor, batch, item, studio, "transcription:subtitle:0"))
    assert item["state"] == "error"
    assert "safetensors required" in item["error"]
    assert item.get("manual_action_required") is not True


def test_a_reserved_node_is_a_wait_not_a_spent_attempt(fixture_monitor, tmp_path, monkeypatch):
    """Gateway 2026-10-02/03: transcriptions failed with "HTTP 409: local is reserved for other
    work on this Mac. Workers attempted: <three nodes>". The peer's MACHINE_BUSY is a door
    refusal - nothing ran - so it must leave the item queued, keep its tries, avoid that Mac
    only briefly and never mark it failed, the way the broker has since 0.6.111."""
    studio = _up_monitor(fixture_monitor)
    failures = []
    monkeypatch.setattr(broker, "mark_external_machine_failure",
                        lambda *args, **kwargs: failures.append(args))

    class BusyNode:
        async def post(self, url, **kwargs):
            return response(409, {"detail": "local is reserved for other work on this Mac",
                                  "code": "MACHINE_BUSY"})

    fixture_monitor._client = BusyNode()
    batch = _subtitle_batch(tmp_path)
    item = batch["items"][0]
    item["tries"] = transcription_jobs.MAX_TRIES   # even on what would be its last try
    asyncio.run(transcription_jobs._run_item(
        fixture_monitor, batch, item, studio, "transcription:subtitle:0"))
    assert item["state"] == "queued", item["error"]
    assert item["tries"] == transcription_jobs.MAX_TRIES - 1
    assert "reserved for other work" in item["error"]
    assert failures == [], "a busy Mac is not a failed Mac"
    machine = studio.get("machine", "local")
    assert time.time() < item["avoid_machines"][machine] <= (
        time.time() + broker.MACHINE_RESERVED_AVOID_S)


def test_packaged_uncertainty_is_preserved_for_subtitle(
        fixture_monitor, tmp_path):
    """A lost response still fences the physical machine for reconciliation."""
    studio = _up_monitor(fixture_monitor)

    class LostClient:
        async def post(self, url, **kwargs):
            raise httpx.ReadTimeout("lost after POST")

    fixture_monitor._client = LostClient()
    batch = _subtitle_batch(tmp_path)
    item = batch["items"][0]
    asyncio.run(asyncio.wait_for(transcription_jobs._run_item(
        fixture_monitor, batch, item, studio, "transcription:subtitle:0"), timeout=30))
    assert item["state"] == "uncertain"
    assert item["tries"] == 0  # dispatch alone increments attempts
    assert item["manual_action_required"] is True
    assert item.get("retry_at") is None
    # The fenced attempt must not be handed to the queue again.
    assert item["state"] != "queued"


def test_cancellation_after_acceptance_does_not_reissue_the_job(
        fixture_monitor, tmp_path):
    studio = _up_monitor(fixture_monitor)
    batch = _subtitle_batch(tmp_path)
    item = batch["items"][0]
    batch["cancelled"] = True
    item["state"] = "running"
    item["studio"] = studio["id"]
    item["studio_task_id"] = "stt-subtitle-batch-0"
    item["tries"] = 1
    result = asyncio.run(transcription_jobs.cancel_batch(batch["id"]))
    # Packaged cancellation keeps the possibly-accepted attempt fenced rather
    # than marking it cancelled-and-safe or queueing it again.
    assert result["items"][0]["state"] == "uncertain"
    assert result["items"][0]["manual_action_required"] is True
