"""Route-level proofs for the additive Hub gaps GatewayKH measured.

These drive the real packaged app over the Fleet credential, so the allowlist,
the middleware and the broker/transcription contracts are exercised together.
"""
from __future__ import annotations

import time

import pytest


@pytest.fixture
def hub(reset):
    from fastapi.testclient import TestClient
    from components.hub.standalone import create_app

    app = create_app(start_background=False)
    with TestClient(app) as client:
        yield client, app.state.monitor


def _fleet_headers() -> dict:
    from components.hub.backend import peers
    return {"Authorization": "Bearer " + peers.fleet_token()}


def _broker_batch(batch_id: str, *, model: str = "fixture/model",
                  items: list[dict] | None = None,
                  cancelled: bool = False) -> dict:
    return {
        "id": batch_id, "modality": "image", "model": model,
        "created_at": time.time(), "cancelled": cancelled, "routing": "pool",
        "items": items or [{
            "index": 0, "state": "queued", "prompt": "p", "params": {},
        }],
    }


def _transcription_item(index: int, state: str, error: str | None = None) -> dict:
    return {
        "index": index, "item_id": f"i{index}", "filename": f"f{index}.wav",
        "input_path": "x", "bytes": 1, "state": state, "tries": 0,
        "studio": None, "studio_task_id": None, "duration_seconds": None,
        "media_duration_seconds": None, "artifact_path": None, "error": error,
        "metadata": None, "avoid_machines": {}, "attempt_history": [],
    }


def _transcription_batch(batch_id: str, *,
                         model: str = "mlx-community/whisper-base-mlx",
                         items: list[dict] | None = None,
                         cancelled: bool = False) -> dict:
    now = time.time()
    return {
        "id": batch_id, "idempotency_key": f"key-{batch_id}", "created_at": now,
        "updated_at": now, "finished_at": None, "cancelled": cancelled,
        "label": None, "project": None, "episode": None, "model": model,
        "language": None, "word_timestamps": False,
        "items": items or [_transcription_item(0, "queued")],
    }


def test_model_id_is_the_same_on_a_model_row_and_a_batch_row(hub, monkeypatch):
    from pathlib import Path

    from components.hub.backend import broker

    # Pin the shipped manifest catalog: another test module may have pointed the
    # env at its own fixture manifests.
    monkeypatch.setenv(
        "STUDIOHUB_MODEL_MANIFESTS",
        str(Path(__file__).resolve().parents[3] / "model-manifests"),
    )
    client, monitor = hub
    monitor._catalog_cache["music"] = (time.time(), {"models": [{
        "repo": "ace-step-1-5-turbo", "label": "ACE-Step",
        "cache": {"state": "cached"}, "available": True,
        "runtime_ready": True, "memory_eligible": True,
    }]})
    broker.batches["b-model"] = _broker_batch("b-model", model="ace-step-1-5-turbo")

    headers = _fleet_headers()
    models = client.get("/api/hub/models", headers=headers)
    assert models.status_code == 200, models.text
    model_row = next(row for row in models.json()["models"]
                     if row["repo"] == "ace-step-1-5-turbo")
    assert model_row["model_id"] == "ACE-Step/Ace-Step1.5"
    assert model_row["repo"] == "ace-step-1-5-turbo"

    jobs = client.get("/api/hub/jobs", headers=headers)
    assert jobs.status_code == 200, jobs.text
    batch_row = next(row for row in jobs.json()["batches"] if row["id"] == "b-model")
    assert batch_row["model_id"] == model_row["model_id"]
    assert batch_row["model"] == "ace-step-1-5-turbo"


def test_batch_rows_list_only_items_that_failed(hub):
    from components.hub.backend import broker, transcription_jobs

    client, _monitor = hub
    broker.batches["b-err"] = _broker_batch("b-err", items=[
        {"index": 0, "state": "done"},
        {"index": 1, "state": "error", "error_code": "MODEL_FILES_UNAVAILABLE",
         "error": "weights missing"},
        {"index": 2, "state": "queued"},
    ])
    broker.batches["b-ok"] = _broker_batch("b-ok", items=[{"index": 0, "state": "done"}])
    transcription_jobs.batches["t-err"] = _transcription_batch(
        "t-err", items=[_transcription_item(0, "error", "bad input"),
                        _transcription_item(1, "done")])

    headers = _fleet_headers()
    jobs = {row["id"]: row for row in
            client.get("/api/hub/jobs", headers=headers).json()["batches"]}
    assert jobs["b-err"]["errors"] == [{
        "item_index": 1, "code": "MODEL_FILES_UNAVAILABLE",
        "message": "weights missing",
    }]
    assert jobs["b-ok"]["errors"] == []

    transcriptions = {row["id"]: row for row in
                      client.get("/api/hub/transcription/jobs",
                                 headers=headers).json()["batches"]}
    assert transcriptions["t-err"]["errors"] == [{
        "item_index": 0, "code": None, "message": "bad input",
    }]


def test_retry_refuses_running_and_uncertain_and_accepts_error(hub):
    from components.hub.backend import broker

    client, _monitor = hub
    headers = _fleet_headers()

    broker.batches["b-run"] = _broker_batch("b-run", items=[{
        "index": 0, "state": "running", "prompt": "p", "params": {},
    }])
    response = client.post("/api/hub/jobs/b-run/items/0/retry", headers=headers)
    assert response.status_code == 400, response.text

    broker.batches["b-unc"] = _broker_batch("b-unc", items=[{
        "index": 0, "state": "uncertain", "prompt": "p", "params": {},
    }])
    response = client.post("/api/hub/jobs/b-unc/items/0/retry", headers=headers)
    assert response.status_code == 400, response.text
    assert "discard" in response.json()["detail"].lower()

    broker.batches["b-cancel"] = _broker_batch("b-cancel", cancelled=True, items=[{
        "index": 0, "state": "error", "prompt": "p", "params": {},
    }])
    response = client.post("/api/hub/jobs/b-cancel/items/0/retry", headers=headers)
    assert response.status_code == 400, response.text
    assert "cancel" in response.json()["detail"].lower()

    failed = {"index": 0, "state": "error", "error": "boom",
              "error_code": "MODEL_FILES_UNAVAILABLE", "prompt": "keep me",
              "params": {"steps": 4}}
    broker.batches["b-fail"] = _broker_batch("b-fail", items=[failed])
    response = client.post("/api/hub/jobs/b-fail/items/0/retry", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": True, "batch_id": "b-fail", "item_index": 0, "state": "queued",
    }
    assert failed["state"] == "queued"
    assert failed["prompt"] == "keep me"
    assert failed["params"] == {"steps": 4}
    assert failed.get("error") is None


def test_transcription_retry_refuses_and_accepts(hub):
    from components.hub.backend import transcription_jobs

    client, _monitor = hub
    headers = _fleet_headers()

    transcription_jobs.batches["t-run"] = _transcription_batch(
        "t-run", items=[_transcription_item(0, "running")])
    response = client.post("/api/hub/transcription/jobs/t-run/items/0/retry",
                           headers=headers)
    assert response.status_code == 400, response.text

    transcription_jobs.batches["t-unc"] = _transcription_batch(
        "t-unc", items=[_transcription_item(0, "uncertain")])
    response = client.post("/api/hub/transcription/jobs/t-unc/items/0/retry",
                           headers=headers)
    assert response.status_code == 400, response.text
    assert "discard" in response.json()["detail"].lower()

    failed = _transcription_item(0, "error", "bad input")
    transcription_jobs.batches["t-fail"] = _transcription_batch("t-fail", items=[failed])
    response = client.post("/api/hub/transcription/jobs/t-fail/items/0/retry",
                           headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": True, "batch_id": "t-fail", "item_index": 0, "state": "queued",
    }
    assert failed["state"] == "queued"
    assert failed.get("error") is None
    assert failed["filename"] == "f0.wav"


def test_model_health_route_is_additive_and_fleet_reachable(hub):
    from components.hub.backend import model_health

    client, monitor = hub
    monitor._catalog_cache["image"] = (time.time(), {"models": [{
        "repo": "fixture/ready", "cache": {"state": "cached"},
        "available": True, "runtime_ready": True, "memory_eligible": True,
    }]})
    response = client.get("/api/hub/model-health", headers=_fleet_headers())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["paused"] == []
    assert payload["models"]
    for row in payload["models"]:
        assert set(row) == {"model_id", "machine", "state", "reason", "hint",
                            "observed_at"}
        assert row["state"] in model_health.STATES


def test_the_new_routes_are_on_the_fleet_allowlist():
    from components.hub.backend import auth

    assert auth.fleet_path_allowed("POST", "/api/hub/jobs/b/items/0/retry")
    assert auth.fleet_path_allowed(
        "POST", "/api/hub/transcription/jobs/b/items/0/retry")
    assert auth.fleet_path_allowed("GET", "/api/hub/model-health")
