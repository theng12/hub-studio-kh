"""Selected model protection contracts from the adopted Hub."""

import httpx
import pytest

from components.hub.backend import broker


@pytest.fixture
def protection(reset):
    broker._model_protection.clear()
    broker.MODEL_PROTECTION_FILE.unlink(missing_ok=True)
    yield
    broker._model_protection.clear()
    broker.MODEL_PROTECTION_FILE.unlink(missing_ok=True)


def test_missing_weights_block_only_this_studio_model_and_survive_reload(protection):
    studio = {"id": "image@node-a", "machine": "node-a"}
    broker._mark_model_failure(studio, "model-a", "abc", "MODEL_FILES_UNAVAILABLE", "Cannot read model files")
    assert broker._model_block_note(studio, "model-a", {})
    assert not broker._model_block_note(studio, "model-b", {})
    assert not broker._model_block_note({"id": "image@node-b"}, "model-a", {})
    broker._model_protection.clear()
    broker._load_model_protection()
    assert broker.model_protection_snapshot()[0]["blocked"] is True
    broker._mark_machine_success(studio)
    assert broker._model_block_note(studio, "model-a", {})


def test_repeated_engine_failures_block_but_one_failure_does_not(protection):
    studio = {"id": "voice@node-a"}
    for _ in range(2):
        broker._mark_model_failure(studio, "model-a", None, "WORKER_TERMINAL_ERROR", "RuntimeError: engine failed")
    assert not broker._model_block_note(studio, "model-a", {})
    broker._mark_model_failure(studio, "model-a", None, "WORKER_TERMINAL_ERROR", "RuntimeError: engine failed")
    assert broker._model_block_note(studio, "model-a", {})


def test_failure_classifier_distinguishes_model_files_from_capacity_and_bad_input(protection):
    assert broker._model_file_failure("RuntimeError: [load_safetensors] Failed to open file /private/model", None)
    assert broker._model_file_failure("RuntimeError: [load_safetensors] Failed to open /private/model/text_encoder/0.safetensors", None)
    assert not broker._model_file_failure("MemoryGuardError: not enough memory", None)
    assert not broker._model_file_failure("ValueError: invalid prompt", None)
    assert broker._worker_terminal_error("missing", "MODEL_FILES_UNAVAILABLE").model_unavailable


def test_recheck_requires_explicit_fresh_readiness_not_legacy_cached(protection, monkeypatch, fixture_monitor):
    import asyncio

    studio = next(s for s in fixture_monitor.registry if s["id"] == "image")
    broker._mark_model_failure(studio, "model-a", None, "MODEL_FILES_UNAVAILABLE", "missing")
    payload = {"models": [{"repo": "model-a", "cache": {"state": "cached"}}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(broker.httpx, "AsyncClient", Client)
    async def exercise():
        with pytest.raises(ValueError, match="readiness"):
            await broker.recheck_model_protection("image", "model-a")
        assert broker._model_block_note(studio, "model-a", {})
        payload["models"][0]["readiness"] = {"ready": True, "revision": "a" * 40}
        assert (await broker.recheck_model_protection("image", "model-a"))["ok"]
        assert not broker._model_block_note(studio, "model-a", {})

    asyncio.run(exercise())


def test_typed_rejection_quarantines_model_without_quarantining_machine(protection, fixture_monitor, monkeypatch):
    import asyncio

    submitted = broker.submit_batch({"modality": "image", "model": "model-a", "items": [{"prompt": "hello"}]})
    batch = broker.batches[submitted["batch_id"]]
    item = batch["items"][0]
    studio = next(s for s in fixture_monitor.registry if s["id"] == "image")
    item.update(state="running", tries=1, studio="image")

    class Client:
        async def post(self, url, **kwargs):
            return httpx.Response(503, json={"detail": {"code": "MODEL_FILES_UNAVAILABLE", "reason": "missing shard"}})

    async def no_finish(*_args, **_kwargs):
        return None

    monkeypatch.setattr(broker, "_post_item_webhook", no_finish)
    monkeypatch.setattr(broker, "_maybe_finish", no_finish)
    async def exercise():
        await broker._run_item(Client(), batch, item, studio)

    asyncio.run(exercise())
    assert item["state"] == "queued"
    assert not item.get("infra_failures")
    assert broker.machine_protection_snapshot() == {}
    assert broker._model_block_note(studio, "model-a", {})


def test_lost_poll_adopts_exact_model_failure_instead_of_transport_failure(protection, fixture_monitor, monkeypatch):
    import asyncio

    submitted = broker.submit_batch({"modality": "image", "model": "model-a", "items": [{"prompt": "hello"}]})
    batch = broker.batches[submitted["batch_id"]]
    item = batch["items"][0]
    studio = next(s for s in fixture_monitor.registry if s["id"] == "image")
    item.update(state="running", tries=1, studio="image")

    class Client:
        polls = 0

        receipt = {"id": "accepted-job", "worker_id": "image", "machine_id": "local",
                   "model_revision": "r" * 40, "runtime_revision": "a" * 64}

        async def post(self, url, **kwargs):
            return httpx.Response(200, json={"job": {**self.receipt, "state": "queued"}})

        async def get(self, url, **kwargs):
            self.polls += 1
            if self.polls == 1:
                raise httpx.RemoteProtocolError("connection dropped")
            return httpx.Response(200, json={"job": {**self.receipt, "state": "error",
                "error_code": "MODEL_FILES_UNAVAILABLE", "error": "missing shard"}})

    async def no_sleep(*args):
        return None

    monkeypatch.setattr(broker.asyncio, "sleep", no_sleep)
    async def exercise():
        await broker._run_item(Client(), batch, item, studio)

    asyncio.run(exercise())
    assert item["state"] == "queued"
    assert item["error_code"] == "MODEL_FILES_UNAVAILABLE"
    assert not item.get("infra_failures")
    assert broker.machine_protection_snapshot() == {}
    assert broker.model_protection_snapshot()[0]["failures"] == 1


def test_engine_failure_window_does_not_extend_with_each_failure(protection, monkeypatch):
    studio = {"id": "voice@node-a"}
    for now in (1000.0, 1240.0, 1480.0):
        monkeypatch.setattr(broker.time, "time", lambda: now)
        broker._mark_model_failure(studio, "model-a", None, "ENGINE_ERROR", "RuntimeError: failed")
    assert not broker._model_block_note(studio, "model-a", {})
    assert broker._model_protection[(studio["id"], "model-a")]["failures"] == 1
