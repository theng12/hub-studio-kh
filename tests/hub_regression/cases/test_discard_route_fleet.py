"""The Fleet credential can discard a real uncertain item through the HTTP route.

This is the exact call GatewayKH's operator action makes, so it exercises the
allowlist, the middleware and the broker together rather than each in isolation.
"""
from __future__ import annotations

import json
import os
from contextlib import ExitStack
from unittest import mock

import pytest


@pytest.fixture
def packaged_app(tmp_path, monkeypatch):
    from components.hub.backend import auth, ledger, monitor as monitor_module, registry
    from components.hub.standalone import create_app

    data = tmp_path / "state"
    data.mkdir()
    token = "hub-owner-token-000000000000"
    token_file = data / ".hub_token"
    token_file.write_text(token + "\n")
    os.chmod(token_file, 0o600)
    workers = tmp_path / "workers.json"
    workers.write_text(json.dumps([{
        "id": "voice", "title": "Voice Studio", "modality": "voice",
        "host": "127.0.0.1", "port": 47870, "machine": "local", "local": True,
        "hub_token": token, "runtime_identity": {"id": "voice-runtime", "compatible": True},
        "managed_output_roots": [],
    }]))
    os.chmod(workers, 0o600)
    monkeypatch.setenv("STUDIOHUB_REQUIRE_AUTH", "1")
    monkeypatch.setenv("STUDIOHUB_PACKAGED", "1")
    monkeypatch.setenv("STUDIOHUB_DATA_DIR", str(data))
    monkeypatch.setenv("STUDIOHUB_WORKERS_FILE", str(workers))
    monkeypatch.setenv("STUDIOHUB_FLEET_TOKEN", "fleet-credential-0000000000000000")
    with ExitStack() as stack:
        for module, name, value in (
            (registry, "DATA_DIR", data),
            (registry, "REGISTRY_FILE", data / "studios.json"),
            (registry, "LABELS_FILE", data / "machine_labels.json"),
            (registry, "FLAGS_FILE", data / "machine_flags.json"),
            (monitor_module, "DATA_DIR", data),
            (auth, "TOKEN_FILE", data / ".hub_token"),
            (auth, "PASSWORD_FILE", data / ".hub_password.json"),
            (auth, "SESSIONS_FILE", data / ".hub_sessions.json"),
            (ledger, "DB_FILE", data / "hub.db"),
            (ledger, "PINOKIO_HOME", data),
        ):
            stack.enter_context(mock.patch.object(module, name, value))
        registry._labels_cache = None
        registry._flags_cache = None
        yield create_app(start_background=False), token


def _uncertain(batch_id: str = "fleet-discard-1") -> dict:
    batch = {"id": batch_id, "modality": "music", "model": "fixture/model",
             "created_at": 1.0, "cancelled": False, "routing": "pool",
             "items": [{"index": 0, "state": "uncertain", "machine": "local",
                        "studio": "voice", "studio_job_id": "worker-1", "tries": 1,
                        "prompt": "p", "params": {}}]}
    return batch


def test_a_fleet_credential_discards_an_uncertain_item(packaged_app):
    from fastapi.testclient import TestClient
    from components.hub.backend import broker, peers

    app, _owner = packaged_app
    batch = _uncertain()
    broker.batches[batch["id"]] = batch
    assert "local" in broker.busy_machines()

    with TestClient(app) as client:
        response = client.post(f"/api/hub/jobs/{batch['id']}/items/0/discard",
                               headers={"Authorization": "Bearer " + peers.fleet_token()})

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    item = batch["items"][0]
    assert item["state"] == "cancelled"
    assert item["error_code"] == "DISCARDED_UNCERTAIN"
    assert "local" not in broker.busy_machines()


def test_the_fleet_credential_cannot_discard_a_live_item(packaged_app):
    from fastapi.testclient import TestClient
    from components.hub.backend import broker, peers

    app, _owner = packaged_app
    batch = _uncertain("fleet-discard-2")
    batch["items"][0]["state"] = "running"
    broker.batches[batch["id"]] = batch

    with TestClient(app) as client:
        response = client.post(f"/api/hub/jobs/{batch['id']}/items/0/discard",
                               headers={"Authorization": "Bearer " + peers.fleet_token()})

    assert response.status_code == 400
    assert batch["items"][0]["state"] == "running"
