"""Every Studio answers ``/api/fleet/activity`` with a snapshot the Hub accepts.

Image, Subtitle and Music used to 404, so a machine with more than one studio
read "partially unavailable" and a joined machine's state fell to unknown.  A
worker with nothing to report must still return a valid empty snapshot; an
older worker's 404 stays the Hub's compatibility path and is not changed here.
"""
from __future__ import annotations

from pathlib import Path
import threading
import time

import pytest
from starlette.testclient import TestClient

from components.hub.backend import activity


ROOT = Path(__file__).resolve().parents[3]
TOKEN = "worker-activity-regression-token"


def _image_manager(tmp_path):
    from components.image.app.backend.generation import GenerationManager

    return GenerationManager(tmp_path / "image", tmp_path, {}, "a" * 64)


def _image_job(state: str = "running") -> dict:
    now = time.time()
    job = {"id": "image-1", "params": {"repo": "org/image"}, "state": state,
           "progress": 1.0 if state == "done" else 0.5,
           "created_at": now - 10, "started_at": now - 9, "finished_at": None,
           "error_code": None}
    if state == "done":
        job["finished_at"] = now - 1
    return job


def _subtitle_manager(tmp_path):
    from components.subtitle.app.backend.manager import SubtitleManager

    return SubtitleManager(tmp_path / "subtitle", tmp_path, [], "b" * 64,
                           voice_app_dir=ROOT / "components" / "voice" / "app")


def _subtitle_job() -> dict:
    now = time.time()
    return {"id": "sub-1", "state": "done", "progress": 1.0,
            "created_at": now - 20, "started_at": now - 18, "finished_at": now - 2,
            "model": "org/whisper", "error_code": None}


def _music_manager(tmp_path):
    from components.music.app.backend.generation import GenerationManager

    return GenerationManager(tmp_path / "music", runtime_sha256="c" * 64)


def _music_job() -> dict:
    now = time.time()
    return {"id": "music-1", "params": {"repo": "org/music"}, "state": "done",
            "progress": 1.0, "created_at": now - 20, "started_at": now - 18,
            "finished_at": now - 2, "error_code": None}


def _voice_manager(*jobs):
    from components.voice.app.backend.generation import GenerationManager

    manager = GenerationManager.__new__(GenerationManager)
    manager._lock = threading.Lock()
    manager._jobs = {job.job_id: job for job in jobs}
    return manager


def _assert_hub_accepts(response, studio: str) -> dict:
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema"] == "kh-studio.activity.v1"
    assert payload["studio"] == studio
    assert activity.validate_snapshot(payload, expected_studio=studio) is not None
    return payload


def test_image_activity_route_answers_and_hub_accepts_it(tmp_path):
    from components.image.app.backend import main as image_main

    manager = _image_manager(tmp_path)
    app = image_main.create_app(manager, TOKEN)
    client = TestClient(app)

    assert client.get("/api/fleet/activity").status_code == 401
    empty = _assert_hub_accepts(
        client.get("/api/fleet/activity", headers={"X-Studio-Token": TOKEN}), "image")
    assert empty["active"] is None and empty["latest"] is None

    manager.jobs["image-1"] = _image_job()
    payload = _assert_hub_accepts(
        client.get("/api/fleet/activity", headers={"X-Studio-Token": TOKEN}), "image")
    assert payload["active"]["id"] == "image-1"
    assert payload["active"]["operation"] == "image"


def test_subtitle_activity_route_answers_and_hub_accepts_it(tmp_path):
    from components.subtitle.app.backend import main as subtitle_main

    manager = _subtitle_manager(tmp_path)
    app = subtitle_main.create_app(manager, TOKEN)
    client = TestClient(app)

    assert client.get("/api/fleet/activity").status_code == 401
    empty = _assert_hub_accepts(
        client.get("/api/fleet/activity", headers={"X-Studio-Token": TOKEN}),
        "transcription")
    assert empty["active"] is None and empty["latest"] is None

    manager.jobs["sub-1"] = _subtitle_job()
    payload = _assert_hub_accepts(
        client.get("/api/fleet/activity", headers={"X-Studio-Token": TOKEN}),
        "transcription")
    assert payload["latest"]["id"] == "sub-1"
    assert payload["latest"]["operation"] == "transcription"


def test_music_activity_route_answers_and_hub_accepts_it(tmp_path):
    from components.music.app.backend import main as music_main

    manager = _music_manager(tmp_path)
    app = music_main.create_app(manager, TOKEN)
    client = TestClient(app)

    assert client.get("/api/fleet/activity").status_code == 401
    empty = _assert_hub_accepts(
        client.get("/api/fleet/activity", headers={"X-Studio-Token": TOKEN}), "music")
    assert empty["active"] is None and empty["latest"] is None

    manager.jobs["music-1"] = _music_job()
    payload = _assert_hub_accepts(
        client.get("/api/fleet/activity", headers={"X-Studio-Token": TOKEN}), "music")
    assert payload["latest"]["id"] == "music-1"
    assert payload["latest"]["operation"] == "music"


def test_voice_activity_route_answers_and_hub_accepts_it(monkeypatch):
    from components.voice.app.backend import main as voice_main
    from components.voice.app.backend.generation import GenerationJob

    monkeypatch.setattr(voice_main, "gen_manager", _voice_manager())
    monkeypatch.setattr(voice_main, "stt_manager", _voice_manager())
    app = voice_main.app

    assert TestClient(app).get("/api/fleet/activity").status_code == 401
    client = TestClient(app, headers={"X-Studio-Token": voice_main.FLEET_TOKEN})
    empty = _assert_hub_accepts(client.get("/api/fleet/activity"), "voice")
    assert empty["active"] is None and empty["latest"] is None

    now = time.time()
    monkeypatch.setattr(voice_main, "gen_manager", _voice_manager(GenerationJob(
        "voice-1", "txt2speech", {"repo": "org/voice"}, state="running",
        progress=0.4, created_at=now - 5, started_at=now - 4)))
    payload = _assert_hub_accepts(client.get("/api/fleet/activity"), "voice")
    assert payload["active"]["id"] == "voice-1"
    assert payload["active"]["operation"] == "speech"
