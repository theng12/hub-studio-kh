"""The Gateway's `tags` field survives `POST /api/hub/shared-voices`, the call it makes.

The Gateway is the one authority for a voice's tags (commits 56c7b7b, a699bd4 on
`fix/gateway-batch-0217-20261030`): it sends a JSON array in the multipart `tags` field. The Hub
keeps them, shows them and fans them out to every Voice Studio, and is never their editor. This
test exercises the route the Gateway calls - the receiver, its auth and the store together.
"""
from __future__ import annotations

import json
import os
from contextlib import ExitStack
from unittest import mock

import pytest

AUDIO = b"RIFF____WAVEfmt " + b"y" * 400


@pytest.fixture
def packaged_app(tmp_path, monkeypatch):
    from components.hub.backend import auth, ledger, monitor as monitor_module, registry, shared_voices
    from components.hub.standalone import create_app

    data = tmp_path / "state"
    data.mkdir()
    token = "fixture-hub-token-for-the-tags-test"
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
    monkeypatch.setenv("STUDIOHUB_FLEET_TOKEN", "fixture-fleet-token-for-the-tags-test")
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
            (shared_voices, "ROOT", tmp_path / "shared_voices"),
        ):
            stack.enter_context(mock.patch.object(module, name, value))
        registry._labels_cache = None
        registry._flags_cache = None
        yield create_app(start_background=False), token


def _push(client, token, *, name, tags=None, audio=AUDIO):
    data = {"name": name, "language": "en", "gender": "m", "license": "self-owned",
            "permission_acknowledged": "true"}
    if tags is not None:
        data["tags"] = tags
    return client.post("/api/hub/shared-voices", data=data,
                       files={"audio": ("reference.wav", audio, "audio/wav")},
                       headers={"Authorization": f"Bearer {token}"})


def test_the_gateways_tags_are_stored_and_answered_back(packaged_app):
    from fastapi.testclient import TestClient
    app, token = packaged_app
    with TestClient(app) as client:
        answer = _push(client, token, name="Mark", tags='["warm", "Khmer", "warm"]')
        assert answer.status_code == 201, answer.text
        assert answer.json()["voice"]["tags"] == ["warm", "Khmer"]
        listed = client.get("/api/hub/shared-voices",
                            headers={"Authorization": f"Bearer {token}"}).json()["voices"]
        assert listed[0]["tags"] == ["warm", "Khmer"]


def test_a_voice_pushed_without_tags_or_with_a_malformed_field_has_none(packaged_app):
    from fastapi.testclient import TestClient
    app, token = packaged_app
    with TestClient(app) as client:
        assert _push(client, token, name="Aiden").json()["voice"]["tags"] == []
        assert _push(client, token, name="Bao", tags="not json",
                     audio=AUDIO + b"2").json()["voice"]["tags"] == []
