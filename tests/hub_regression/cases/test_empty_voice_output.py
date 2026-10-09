"""An empty or unplayable voice file is a failed generation, never a published "done" (0.6.164).

2026-10-09 08:04, Hub batch ebcfd92c1a on studio-mac-1504: a Breeze TTS job delivered a 0-byte m4a.
The Hub set `artifact_metadata_error` but published the item `done` ("never retry a completed
generation") and served the empty file with no size or checksum. Now:

- 0 bytes -> the attempt fails `EMPTY_OUTPUT`; an unreadable container -> `INVALID_OUTPUT`. Both
  are retryable worker failures, so the normal retry and reroute applies.
- A non-empty, readable file whose metadata check merely could not finish stays `done`, unbilled,
  as before - and now still carries the size and SHA-256 it is served with.
"""
from __future__ import annotations

import asyncio
import hashlib
import stat

import httpx
import pytest

from components import audio_encode
from components.hub.backend import broker
from tests.hub_regression.cases.test_voice_durability import (
    WorkerClient, completed_voice_job, voice_attempt, wav_bytes)


def _box(kind: bytes, body: bytes) -> bytes:
    return (8 + len(body)).to_bytes(4, "big") + kind + body


FTYP = b"\x00\x00\x00\x1cftypM4A \x00\x00\x00\x00M4A mp42isom"
M4A = FTYP + _box(b"moov", b"\x22" * 300) + _box(b"free", b"") + _box(b"mdat", b"\x11" * 4000)
NO_MOOV = FTYP + _box(b"free", b"") + _box(b"mdat", b"\x11" * 4000)


class StreamingWorker(WorkerClient):
    """`WorkerClient`, plus the streamed download the compressed (m4a) path uses."""

    def stream(self, method, url, **kwargs):
        worker = self

        class Context:
            async def __aenter__(self):
                worker.artifact_gets += 1
                if isinstance(worker.artifact, BaseException):
                    raise worker.artifact

                async def chunks(size=65536):
                    for start in range(0, len(worker.artifact), 7):
                        yield worker.artifact[start:start + 7]

                return type("R", (), {"raise_for_status": lambda self: None,
                                      "aiter_bytes": lambda self, size=65536: chunks(size)})()

            async def __aexit__(self, *args):
                return False

        return Context()


def _m4a_job(finished: dict, payload: bytes, **changes) -> dict:
    finished.update({"media_type": "audio/mp4", "format": "m4a",
                     "output_path": "/tmp/voice-worker-1.m4a", "bytes": len(payload),
                     "sha256": hashlib.sha256(payload).hexdigest(), "audio_duration_s": 4.2,
                     "audio_duration_ms": 4200, "sample_rate_hz": 24000, "channels": 1,
                     **changes})
    return finished


def _run(fixture_monitor, monkeypatch, artifact, change=lambda job: job, tries=1):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    item["tries"] = tries
    finished = change(completed_voice_job(batch, item, accepted))
    client = StreamingWorker(accepted, polled=finished, artifact=artifact)

    async def no_wait(*args):
        return None

    monkeypatch.setattr(broker.asyncio, "sleep", no_wait)
    monkeypatch.setattr(broker, "_post_item_webhook", no_wait)
    monkeypatch.setattr(broker, "_maybe_finish", no_wait)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)
    asyncio.run(broker._run_item(client, batch, item, studio))
    return batch, item, client


@pytest.mark.parametrize("artifact, change, code", [
    # The 1504 case: the worker itself reports a 0-byte m4a.
    (b"", lambda job: _m4a_job(job, b""), "EMPTY_OUTPUT"),
    # The worker describes a real file, but what it serves is empty.
    (b"", lambda job: _m4a_job(job, M4A), "EMPTY_OUTPUT"),
    (b"", lambda job: job, "EMPTY_OUTPUT"),                        # WAV path
    (NO_MOOV, lambda job: _m4a_job(job, NO_MOOV), "INVALID_OUTPUT"),  # "moov atom not found"
    (M4A[:-100], lambda job: _m4a_job(job, M4A), "INVALID_OUTPUT"),  # cut off
    (b"<html>502</html>", lambda job: _m4a_job(job, M4A), "INVALID_OUTPUT"),
    (b"not a wav artifact", lambda job: job, "INVALID_OUTPUT"),    # WAV path
], ids=["worker-says-0", "served-empty-m4a", "served-empty-wav", "no-moov", "cut-off-m4a",
        "html-as-m4a", "junk-wav"])
def test_an_unusable_file_fails_the_attempt_and_is_retried(
        fixture_monitor, monkeypatch, artifact, change, code):
    batch, item, client = _run(fixture_monitor, monkeypatch, artifact, change)
    assert item["state"] == "queued", item            # the normal retry, possibly elsewhere
    assert item["error_code"] == code
    assert "unusable" in item["error"]
    assert not item.get("asset_id")
    assert item.get("artifact_url") is None and item.get("sha256") is None
    assert broker.terminal_result(batch, item) is None


def test_the_last_try_ends_failed_with_the_code(fixture_monitor, monkeypatch):
    batch, item, _ = _run(fixture_monitor, monkeypatch, b"", lambda job: _m4a_job(job, b""),
                          tries=broker.MAX_TRIES)
    assert item["state"] == "error"
    assert item["error_code"] == "EMPTY_OUTPUT"
    assert broker.public_item(batch, item)["error_code"] == "EMPTY_OUTPUT"


def test_a_good_m4a_is_done_with_size_and_checksum(fixture_monitor, monkeypatch):
    batch, item, _ = _run(fixture_monitor, monkeypatch, M4A, lambda job: _m4a_job(job, M4A))
    assert item["state"] == "done" and "artifact_metadata_error" not in item
    result = broker.terminal_result(batch, item)
    assert (result["bytes"], result["sha256"]) == (len(M4A), hashlib.sha256(M4A).hexdigest())


@pytest.mark.parametrize("artifact, change", [
    # Readable files whose metadata check could not complete: still a completed generation.
    (M4A, lambda job: _m4a_job(job, M4A, audio_duration_s=None)),
    (M4A, lambda job: _m4a_job(job, M4A, bytes=17)),
    (wav_bytes(), lambda job: job.update(sha256="0" * 64) or job),
], ids=["m4a-no-duration", "m4a-byte-count-mismatch", "wav-hash-mismatch"])
def test_a_readable_file_with_a_metadata_hiccup_stays_done_and_carries_its_digest(
        fixture_monitor, monkeypatch, artifact, change):
    batch, item, _ = _run(fixture_monitor, monkeypatch, artifact, change)
    assert item["state"] == "done", item
    assert item["artifact_metadata_error"]
    assert item.get("audio_duration_s") is None                    # unverified, unbillable
    result = broker.terminal_result(batch, item)
    assert (result["bytes"], result["sha256"]) == (len(artifact),
                                                   hashlib.sha256(artifact).hexdigest())


def test_a_download_that_cannot_run_keeps_the_workers_digest(fixture_monitor, monkeypatch):
    """The Hub could not fetch the file to check it: done, with the worker's own size and hash."""
    batch, item, _ = _run(fixture_monitor, monkeypatch, httpx.ConnectError("offline"),
                          lambda job: _m4a_job(job, M4A))
    assert item["state"] == "done" and item["artifact_metadata_error"]
    assert (item["bytes"], item["sha256"]) == (len(M4A), hashlib.sha256(M4A).hexdigest())


def test_recovery_reports_an_unusable_file_as_a_failed_job(fixture_monitor, monkeypatch):
    """A job found finished after a dropped poll is checked the same way, without a 120 s loop."""
    monkeypatch.setattr(broker, "RECOVERY_WINDOW_S", 2.0)
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    item["studio_job_id"] = accepted["id"]
    finished = _m4a_job(completed_voice_job(batch, item, accepted), b"")
    client = StreamingWorker(accepted, polled=finished, artifact=b"")
    body = {"client_request_id": finished["params"]["client_request_id"]}
    recovered = asyncio.run(broker._recover_worker_job(client, batch, item, studio, body, 0.0))
    assert isinstance(recovered, dict) and recovered["state"] == "error"
    assert recovered["error_code"] == "EMPTY_OUTPUT"
    assert item.get("state") != "done" and not item.get("asset_id")


# ── the encoder ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("data, problem", [
    (M4A, None), (b"", "empty"), (NO_MOOV, "moov"), (M4A[:-1], "cut off"),
    (b"<html>no</html>", "ftyp"),
], ids=["good", "empty", "no-moov", "cut-off", "html"])
def test_the_box_walk_in_any_chunking(data, problem):
    for size in (1, 3, 8, 16, 4096):
        boxes = audio_encode.Mp4Boxes()
        for start in range(0, len(data), size):
            boxes.feed(data[start:start + size])
        found = boxes.problem()
        assert (found is None) if problem is None else (problem in found), (size, found)


def test_the_encoder_refuses_an_mp4_without_moov(tmp_path, monkeypatch):
    """ffmpeg exiting 0 with a moov-less file (and no ffprobe to notice) must not be delivered."""
    fake = tmp_path / "ffmpeg"
    fake.write_bytes(b"#!/bin/sh\nfor last; do :; done\nprintf '"
                     + b"".join(b"\\%03o" % byte for byte in NO_MOOV) + b"' > \"$last\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("STUDIOHUB_FFMPEG", str(fake))
    monkeypatch.setattr(audio_encode, "probe_duration", lambda *a, **k: None)
    master = tmp_path / "master.wav"
    master.write_bytes(wav_bytes())
    with pytest.raises(audio_encode.EncodeError, match="moov"):
        audio_encode.encode_m4a(master, tmp_path / "out.m4a", profile="narration",
                                expected_seconds=0.1)
    assert not (tmp_path / "out.m4a").exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".partial")] == []
