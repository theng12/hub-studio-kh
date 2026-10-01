"""Controller side of compress-at-source (`output_format`): receipts, dispatch, recovery, metadata."""
from __future__ import annotations

import asyncio
import hashlib
import time

import httpx
import pytest

from components.hub.backend import artifact_metadata, broker


def response(status, payload):
    return httpx.Response(status, json=payload,
                          request=httpx.Request("GET", "http://127.0.0.1/"))


class StopBeforePoll(BaseException):
    pass


# ── receipts ────────────────────────────────────────────────────────────────────────────────

def _done(job_id, url, media_type, path):
    return {"id": job_id, "state": "done", "output_url": url, "output_path": path,
            "media_type": media_type, "generated_at": time.time(), "bytes": 10,
            "sha256": "a" * 64}


@pytest.mark.parametrize("media_type, ok", [
    ("audio/wav", True), ("audio/flac", True), ("audio/mp4", True), ("audio/mpeg", False)])
def test_music_receipt_accepts_the_native_and_compressed_files(media_type, ok):
    item = {"studio_job_id": "j1"}
    job = _done("j1", "/api/generate/jobs/j1/audio", media_type, "/m/j1.bin")
    if ok:
        broker._validate_music_worker_job(item, job)
    else:
        with pytest.raises(ValueError):
            broker._validate_music_worker_job(item, job)


@pytest.mark.parametrize("media_type, ok", [
    ("image/png", True), ("image/jpeg", True), ("image/webp", False)])
def test_image_receipt_accepts_png_and_jpeg(media_type, ok):
    item = {"studio_job_id": "j1"}
    job = _done("j1", "/api/generate/jobs/j1/image", media_type, "/i/j1.jpg")
    if ok:
        broker._validate_image_worker_job(item, job)
    else:
        with pytest.raises(ValueError):
            broker._validate_image_worker_job(item, job)


def test_a_sniffed_m4a_is_audio_not_video():
    m4a = b"\x00\x00\x00\x1cftypM4A \x00\x00\x00\x00M4A mp42isom"
    mp4 = b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2avc1"
    assert artifact_metadata.sniff_media_type(m4a) == "audio/mp4"
    assert artifact_metadata.sniff_media_type(mp4) == "video/mp4"


# ── advertised support ──────────────────────────────────────────────────────────────────────

def _entry(*values):
    parameter = {"name": "output_format", "type": "string", "enum": list(values)}
    return {"repo": "m", "lane_contract": {"parameters": [{"name": "prompt"}, parameter]}}


@pytest.mark.parametrize("entry, value, expected", [
    (_entry("wav", "m4a"), "m4a", True),
    (_entry("png", "jpeg"), "jpeg", True),
    (_entry("png", "jpeg"), "m4a", False),
    ({"repo": "m", "lane_contract": {"parameters": [{"name": "prompt"}]}}, "m4a", False),
    ({"repo": "m"}, "m4a", False),
    (None, "m4a", False),
])
def test_only_a_worker_that_lists_the_value_is_sent_it(monkeypatch, entry, value, expected):
    async def catalog_entry(studio, model):
        return entry

    monkeypatch.setattr(broker, "_catalog_entry", catalog_entry)
    assert asyncio.run(broker._lane_accepts_output_format({"id": "s"}, "m", value)) is expected


class PostRecorder:
    def __init__(self, accepted):
        self.accepted = accepted
        self.bodies = []

    async def post(self, *args, **kwargs):
        self.bodies.append(dict(kwargs.get("json") or {}))
        return response(200, {"job": self.accepted})

    async def get(self, *args, **kwargs):
        raise AssertionError("no poll expected")


def _music_dispatch(fixture_monitor, monkeypatch, entry):
    studio = next(row for row in fixture_monitor.registry if row["id"] == "music")

    async def catalog_entry(_studio, _model):
        return entry

    async def stop(*args):
        raise StopBeforePoll()

    monkeypatch.setattr(broker, "_catalog_entry", catalog_entry)
    monkeypatch.setattr(broker.asyncio, "sleep", stop)
    monkeypatch.setattr(broker.ledger, "save_batch", lambda value: None)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)
    batch = {"id": "of-proof", "client_request_id": "of-req", "modality": "music",
             "model": "stabilityai/stable-audio-3-optimized",
             "shared_params": {"output_format": "m4a"}, "cancelled": False, "routing": "pool",
             "created_at": time.time(), "items": []}
    item = {"index": 0, "prompt": "proof", "params": {}, "seed": None, "state": "running",
            "tries": 1, "studio": studio["id"], "studio_job_id": None, "retry_at": None,
            "error": None}
    batch["items"] = [item]
    broker.batches[batch["id"]] = batch
    broker._busy.add(studio["id"])
    accepted = {"id": "w1", "state": "queued", "worker_id": studio["id"],
                "machine_id": studio["machine"], "model_revision": "r" * 40,
                "runtime_revision": "a" * 64, "params": {"prompt": "proof"}}
    client = PostRecorder(accepted)
    with pytest.raises(StopBeforePoll):
        asyncio.run(broker._run_item(client, batch, item, studio))
    return client.bodies[0]


def test_dispatch_sends_output_format_to_a_worker_that_advertises_it(fixture_monitor, monkeypatch):
    body = _music_dispatch(fixture_monitor, monkeypatch, _entry("wav", "m4a"))
    assert body["output_format"] == "m4a"


def test_dispatch_strips_output_format_for_a_worker_that_does_not(fixture_monitor, monkeypatch):
    body = _music_dispatch(fixture_monitor, monkeypatch, {"repo": "m", "lane_contract": {
        "parameters": [{"name": "prompt"}]}})
    assert "output_format" not in body
    assert body["prompt"] == "proof"


# ── image recovery ──────────────────────────────────────────────────────────────────────────

class RecoveryClient:
    def __init__(self, job):
        self.job = job

    async def get(self, *args, **kwargs):
        return response(200, {"job": self.job})

    async def delete(self, *args, **kwargs):
        return response(200, {"ok": True})


def test_an_old_worker_that_never_echoed_output_format_is_still_adopted(
        fixture_monitor, monkeypatch):
    studio = next(row for row in fixture_monitor.registry if row["id"] == "image")
    batch = {"id": "image-of", "client_request_id": "orig", "modality": "image",
             "model": "fixture/flux", "shared_params": {"output_format": "jpeg"},
             "cancelled": False, "routing": "pool", "created_at": time.time(), "items": []}
    item = {"index": 0, "prompt": "proof", "params": {}, "seed": -1, "state": "uncertain",
            "tries": 1, "studio": studio["id"], "studio_job_id": "worker-1",
            "retry_at": None, "error": None, "machine": studio["machine"],
            "worker_id": studio["id"], "model_revision": "r" * 40,
            "runtime_revision": "a" * 64,
            "recovery": {"phase": "uncertain", "reason": "lost poll"}}
    batch["items"] = [item]
    broker.batches[batch["id"]] = batch
    adopted = []

    async def adopt(client, b, i, s, job, body, started, *, recovery=False):
        adopted.append(job["id"])
        i["state"] = "done"

    async def noop(*args):
        return None

    monkeypatch.setattr(broker, "_record_worker_success", adopt)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)
    # An old worker: its echoed params have no `output_format`, and it delivered the PNG.
    job = {"id": "worker-1", "state": "done", "model_revision": item["model_revision"],
           "runtime_revision": item["runtime_revision"], "worker_id": studio["id"],
           "machine_id": studio["machine"],
           "params": {"repo": batch["model"], "prompt": "proof", "seed": -1},
           "output_url": "/api/generate/jobs/worker-1/image", "output_path": "/tmp/w1.png",
           "media_type": "image/png", "generated_at": time.time(), "bytes": 1, "sha256": "a" * 64}
    result = asyncio.run(broker.reconcile_image_item(batch["id"], 0, RecoveryClient(job)))
    assert adopted == ["worker-1"], result
    assert result["reconciled"]


# ── voice metadata ──────────────────────────────────────────────────────────────────────────

class StreamingClient:
    """Serves one artifact only through ``stream``; ``get`` would buffer it and must not run."""

    def __init__(self, payload):
        self.payload = payload
        self.streams = 0

    async def get(self, *args, **kwargs):
        raise AssertionError("a compressed artifact must never be downloaded whole")

    def stream(self, method, url, **kwargs):
        client = self

        class Context:
            async def __aenter__(self):
                client.streams += 1

                async def chunks(size):
                    for start in range(0, len(client.payload), size):
                        yield client.payload[start:start + size]

                return type("R", (), {"raise_for_status": lambda self: None,
                                      "aiter_bytes": lambda self, size=65536: chunks(size)})()

            async def __aexit__(self, *args):
                return False

        return Context()


M4A = b"\x00\x00\x00\x1cftypM4A \x00\x00\x00\x00M4A mp42isom" + b"\x11" * 5000


def _m4a_job(payload=M4A, **changes):
    return {"media_type": "audio/mp4", "format": "m4a", "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(), "audio_duration_s": 81.5,
            "audio_duration_ms": 81500, "sample_rate_hz": 24000, "channels": 1, **changes}


def _verify(client, job):
    item = {}
    studio = {"id": "voice", "host": "127.0.0.1", "port": 51202, "machine": "local",
              "studio_token": "t", "hub_token": "t"}
    asyncio.run(broker._cache_voice_artifact_metadata(
        client, item, studio, "http://127.0.0.1:51202/api/generate/jobs/j/audio",
        job["bytes"], job["sha256"], job))
    return item


def test_an_m4a_is_streamed_and_keeps_the_workers_decoded_facts():
    client = StreamingClient(M4A)
    item = _verify(client, _m4a_job())
    assert client.streams == 1
    assert "artifact_metadata_error" not in item
    assert (item["media_type"], item["format"]) == ("audio/mp4", "m4a")
    assert (item["bytes"], item["sha256"]) == (len(M4A), hashlib.sha256(M4A).hexdigest())
    assert (item["audio_duration_s"], item["audio_duration_ms"]) == (81.5, 81500)
    assert (item["sample_rate_hz"], item["channels"]) == (24000, 1)


@pytest.mark.parametrize("change", [
    {"sha256": "0" * 64}, {"bytes": 17}, {"audio_duration_s": None}, {"channels": 5},
    {"media_type": "audio/mpeg2"}])
def test_an_unverifiable_m4a_is_marked_unbillable(change):
    item = _verify(StreamingClient(M4A), _m4a_job(**change))
    assert item.get("artifact_metadata_error")
    assert item.get("audio_duration_s") is None


def test_a_wav_report_still_takes_the_decoding_path(monkeypatch):
    """The default (and an old worker that reports no type) is byte-for-byte the old behaviour."""
    calls = []

    class Client:
        async def get(self, *args, **kwargs):
            calls.append("get")
            raise httpx.ConnectError("offline")

        def stream(self, *args, **kwargs):
            raise AssertionError("WAV is not streamed")

    for reported in ({"media_type": "audio/wav"}, {}):
        item = _verify(Client(), {"bytes": 4, "sha256": "a" * 64, **reported})
        assert item["artifact_metadata_error"]
    assert calls == ["get", "get"]
