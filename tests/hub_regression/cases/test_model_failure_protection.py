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


def test_no_audio_for_a_tiny_section_does_not_pause_the_model(protection):
    """2026-10-04: a "***" section made Qwen 0.6B write no audio; three such jobs paused the model.

    The worker now reports that as a ValueError (the text, not the model); the same failure from a
    real-length section is still a model failure and still pauses it after the threshold.
    """
    studio = {"id": "voice@mac-1305"}
    batch = {"model": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"}
    content = ("RuntimeError: ValueError: qwen3-tts produced no audio. 3-character section. "
               "the section '***' is too short or has nothing to speak")
    for _ in range(broker.MODEL_FAILURE_THRESHOLD):
        broker._classify_worker_failure(batch, {}, studio, {"state": "error", "error": content}, 0.0)
    assert not broker._model_block_note(studio, batch["model"], {})
    engine = "RuntimeError: RuntimeError: qwen3-tts produced no audio. 240-character section"
    for _ in range(broker.MODEL_FAILURE_THRESHOLD):
        broker._classify_worker_failure(batch, {}, studio, {"state": "error", "error": engine}, 0.0)
    assert broker._model_block_note(studio, batch["model"], {})


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


def test_a_pause_whose_failure_was_the_text_is_lifted_on_start(protection):
    """0.6.143: Qwen 0.6B was paused on mac-1305 and -1408 by a "***" section that wrote no
    audio (reported then as a RuntimeError). On start the Hub re-reads each pause with today's
    classifier: that one is lifted, a real engine failure and missing weights stay paused."""
    import json

    tiny = ("RuntimeError: qwen3-tts produced no audio. 3-character section. host has 17.2 GB "
            "unified memory, 7.5 GB free. this is usually the request being too large for the "
            "available memory")
    engine = "RuntimeError: qwen3-tts produced no audio. 240-character section"
    rows = [
        {"studio": "voice@mac-1305", "model": "qwen", "reason": tiny, "blocked": True,
         "error_code": "WORKER_TERMINAL_ERROR", "failures": 3},
        {"studio": "voice@mac-1408", "model": "qwen", "reason": engine, "blocked": True,
         "error_code": "WORKER_TERMINAL_ERROR", "failures": 3},
        {"studio": "image@node-a", "model": "flux", "reason": "missing", "blocked": True,
         "error_code": "MODEL_FILES_UNAVAILABLE", "failures": 1},
    ]
    broker.MODEL_PROTECTION_FILE.write_text(json.dumps(rows))
    broker._load_model_protection()

    assert not broker._model_block_note({"id": "voice@mac-1305"}, "qwen", {})
    assert broker._model_block_note({"id": "voice@mac-1408"}, "qwen", {})
    assert broker._model_block_note({"id": "image@node-a"}, "flux", {})
    # The lift is saved, so it is done once, not on every start.
    saved = json.loads(broker.MODEL_PROTECTION_FILE.read_text())
    assert {row["studio"] for row in saved} == {"voice@mac-1408", "image@node-a"}


def test_an_older_worker_reporting_no_audio_for_a_tiny_section_does_not_pause(protection):
    """A node still on the pre-fix Voice worker reports the tiny section as a RuntimeError."""
    studio = {"id": "voice@mac-1305"}
    batch = {"model": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"}
    old = "RuntimeError: qwen3-tts produced no audio. 3-character section. host has 8.6 GB"
    for _ in range(broker.MODEL_FAILURE_THRESHOLD):
        broker._classify_worker_failure(batch, {}, studio, {"state": "error", "error": old}, 0.0)
    assert not broker._model_block_note(studio, batch["model"], {})
