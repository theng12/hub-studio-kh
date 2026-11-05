"""The submit door answers ordinary malformed input with a coded 4xx, never a bare 500.

Found during the 2026-11-05 TN0407 hunt. That incident's cause is unknown and this file is not
evidence that TN0407 was caused by malformed input; it guards the door's shape and durability
contracts on their own.

Fake-only: broker submission is patched where a test needs it, so nothing is queued to a studio
and no model or network is touched. State paths come from this directory's conftest (a
disposable temp data dir); the autouse fixture puts the queue back exactly as it found it.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub import standalone  # noqa: E402
from components.hub.backend import auth, broker, control_plane, startup_control  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_queue():
    """Snapshot before the test (and before `reset` runs), restore after it: no leak either way."""
    saved = dict(broker.batches)
    yield
    broker.batches.clear()
    broker.batches.update(saved)


@pytest.fixture
def door(monkeypatch):
    monkeypatch.setattr(standalone.auth, "presented_owner_token", lambda request, token: True)
    monkeypatch.setattr(control_plane, "accepts_customer_jobs", lambda: True)
    monkeypatch.setattr(standalone, "_submission_readiness", lambda envelope, monitor: None)
    monkeypatch.setattr(broker, "every_capable_mac_paused", lambda *a, **k: False)
    # TestClient is not loopback, so present this Mac's own Hub token like the app does.
    return TestClient(standalone.app, headers={"x-hub-token": auth.load_token()})


def _envelope(**extra):
    return {"modality": "voice", "model": "fixture/voice", "items": [{"text": "hello"}], **extra}


def test_a_string_item_is_refused_with_a_code_not_a_500(door):
    response = door.post("/api/hub/jobs", json=_envelope(items=["hello"]))
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_ITEMS"


def test_empty_items_is_refused_with_a_code(door):
    response = door.post("/api/hub/jobs", json=_envelope(items=[]))
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_ITEMS"


def test_item_params_must_be_an_object(door):
    response = door.post("/api/hub/jobs",
                         json=_envelope(items=[{"text": "hello", "params": "x"}]))
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_ITEM_PARAMS"


def test_shared_params_must_be_an_object(door):
    response = door.post("/api/hub/jobs", json=_envelope(sharedParams="ab"))
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_SHARED_PARAMS"


def test_a_wrong_typed_modality_is_coded_not_a_500(door):
    response = door.post("/api/hub/jobs", json=_envelope(modality=["voice"]))
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_MODALITY"


def test_a_wrong_typed_model_is_coded_not_a_500(door):
    response = door.post("/api/hub/jobs", json=_envelope(model={"repo": "x"}))
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_MODEL"


def test_a_valid_envelope_reaches_the_broker_unchanged(door, monkeypatch):
    seen = {}

    def submit(envelope):
        seen.update(envelope)
        return {"batch_id": "b1", "items": 1, "replayed": False}

    monkeypatch.setattr(broker, "submit_batch", submit)
    response = door.post("/api/hub/jobs", json=_envelope(clientRequestId="valid-shape-1"))
    assert response.status_code == 200, response.text
    assert response.json()["batch_id"] == "b1"
    assert seen["model"] == "fixture/voice"
    assert seen["items"] == [{"text": "hello"}]
    assert seen["clientRequestId"] == "valid-shape-1"


def test_an_unexpected_failure_is_coded_and_audited_without_the_payload(door, monkeypatch):
    calls = []
    monkeypatch.setattr(startup_control, "audit",
                        lambda event, fields: calls.append((event, fields)))

    def explode(_envelope):
        raise RuntimeError("prompt text that must not be published")

    monkeypatch.setattr(broker, "submit_batch", explode)
    response = door.post("/api/hub/jobs", json=_envelope(clientRequestId="correlate-1"))
    assert response.status_code == 500, response.text
    body = response.json()["detail"]
    assert body["code"] == "SUBMIT_FAILED"
    assert "RuntimeError" in body["detail"]
    assert "prompt text" not in response.text
    assert calls and calls[0][0] == "batch_submit_failed"
    assert calls[0][1] == {"client_request_id": "correlate-1", "modality": "voice",
                           "model": "fixture/voice", "item_count": 1,
                           "exception": "RuntimeError"}


def test_a_store_failure_is_retryable_and_does_not_publish_the_exception_text(door, monkeypatch):
    monkeypatch.setattr(startup_control, "audit", lambda event, fields: None)

    def explode(_envelope):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(broker, "submit_batch", explode)
    response = door.post("/api/hub/jobs", json=_envelope())
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "SUBMIT_STORE_FAILED"
    assert "unable to open" not in response.text


def test_direct_broker_callers_get_the_same_coded_refusal():
    unhashable = broker.submit_batch({"modality": ["voice"], "model": "x",
                                      "items": [{"text": "a"}]})
    assert unhashable["code"] == "INVALID_MODALITY"
    not_a_list = broker.submit_batch({"modality": "voice", "model": "x", "items": 5})
    assert not_a_list["code"] == "INVALID_ITEMS"


def test_a_store_failure_publishes_nothing_and_retry_stays_coded(door, monkeypatch, reset):
    monkeypatch.setattr(startup_control, "audit", lambda event, fields: None)

    def boom(_batch):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(broker.ledger, "save_batch", boom)
    before = set(broker.batches)
    response = door.post("/api/hub/jobs", json=_envelope(clientRequestId="store-fail-1"))
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "SUBMIT_STORE_FAILED"
    assert set(broker.batches) == before
    retry = door.post("/api/hub/jobs", json=_envelope(clientRequestId="store-fail-1"))
    assert retry.status_code == 503, retry.text


def test_a_bind_failure_publishes_nothing_and_never_replays_as_success(door, monkeypatch, reset):
    monkeypatch.setattr(startup_control, "audit", lambda event, fields: None)

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("identity store unavailable")

    monkeypatch.setattr(broker.execution_identity, "bind_local_batch", boom)
    before = set(broker.batches)
    response = door.post("/api/hub/jobs", json=_envelope(clientRequestId="bind-fail-1"))
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "SUBMIT_STORE_FAILED"
    assert set(broker.batches) == before
    retry = door.post("/api/hub/jobs", json=_envelope(clientRequestId="bind-fail-1"))
    assert retry.status_code == 503, retry.text


def test_bind_and_persist_run_before_publication(monkeypatch, reset):
    events = []
    monkeypatch.setattr(broker.execution_identity, "bind_local_batch",
                        lambda evidence, batch_id: events.append(("bind", batch_id in broker.batches)))
    monkeypatch.setattr(broker.ledger, "save_batch",
                        lambda batch: events.append(("save", batch["id"] in broker.batches)))
    submitted = broker.submit_batch({"modality": "voice", "model": "fixture/voice",
                                     "items": [{"text": "hello"}]})
    assert submitted["batch_id"] in broker.batches
    assert events == [("bind", False), ("save", False)]


def test_a_stored_batch_reloads_after_restart_and_same_id_retry_replays(reset):
    envelope = {"modality": "voice", "model": "fixture/voice",
                "clientRequestId": "restart-retry-1", "items": [{"text": "hello"}]}
    first = broker.submit_batch(envelope)
    assert "batch_id" in first, first
    broker.batches.clear()
    broker.restore_batches()
    assert first["batch_id"] in broker.batches
    again = broker.submit_batch(envelope)
    assert again.get("replayed") is True, again
    assert again["batch_id"] == first["batch_id"]
