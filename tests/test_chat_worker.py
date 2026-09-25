"""Local Chat Studio worker — engine, memory policy and API contract.

Runs entirely without model weights: the engine's filesystem/telemetry paths and the
FastAPI contract are exercised with a stub engine, so this suite is valid on any host and
does not pretend to be the missing packaged-inference proof.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from components.chat.app.backend import memory_policy as memory_policy_module
from components.chat.app.backend.llm_engine import (DEFAULT_TEMPERATURE, DEFAULT_TOP_P,
                                                    ChatBusy, ChatContextExceeded, ChatEngine,
                                                    ChatModelUnavailable,
                                                    engine_from_environment,
                                                    resolve_model_dir)
from components.chat.app.backend.main import create_app


TOKEN = "test-owner-token"
REVISION = "8b2b98c00a6b4d291155e4890773ca8f769aee53"


class EngineFileTests(unittest.TestCase):
    def test_preflight_reports_each_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "model"
            engine = ChatEngine(model_dir=model_dir, model_id="m", revision="r")
            self.assertEqual(engine.preflight()["state"], "missing")
            model_dir.mkdir()
            (model_dir / "config.json").write_text("{}")
            self.assertEqual(engine.preflight()["state"], "incomplete")
            (model_dir / "model.safetensors").write_bytes(b"weights")
            (model_dir / "tokenizer.json").write_text("{}")
            self.assertEqual(engine.preflight()["state"], "files_verified")

    def test_context_window_reads_nested_text_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text(
                json.dumps({"text_config": {"max_position_embeddings": 262144}}))
            engine = ChatEngine(model_dir=model_dir, model_id="m", revision="r")
            self.assertEqual(engine.context_window_tokens, 262144)
            self.assertEqual(engine.max_output_tokens, 4096)

    def test_context_window_absent_is_none_not_invented(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text("{}")
            engine = ChatEngine(model_dir=model_dir, model_id="m", revision="r")
            self.assertIsNone(engine.context_window_tokens)

    def test_missing_files_refuse_rather_than_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = ChatEngine(model_dir=Path(tmp) / "model", model_id="m", revision="r")
            with self.assertRaises(ChatModelUnavailable):
                engine.complete([{"role": "user", "content": "hi"}])

    def test_resolve_model_dir_matches_library_layout(self) -> None:
        path = resolve_model_dir(Path("/models"), "qwen3.5-9b-optiq-4bit", REVISION)
        self.assertEqual(path, Path("/models") / "qwen3.5-9b-optiq-4bit" / REVISION / "model")


class _FakeEngine:
    model_id = "mlx-community/Qwen3.5-9B-4bit"
    revision = REVISION
    context_window_tokens = 262144
    max_output_tokens = 4096

    def __init__(self, *, verified: bool = False) -> None:
        self._verified = verified
        self._loaded = False
        self._busy = False
        self._last_activity: float | None = None

    def preflight(self) -> dict:
        return {"state": "files_verified" if self._verified else "missing"}

    def loaded(self) -> bool:
        return self._loaded

    def last_activity_at(self):
        return self._last_activity

    def status(self) -> dict:
        return {"model_id": self.model_id, "revision": self.revision, "loaded": self._loaded,
                "busy": self._busy, "loaded_at": None, "active_memory_bytes": None,
                "peak_memory_bytes": None, "context_window_tokens": self.context_window_tokens,
                "max_output_tokens": self.max_output_tokens, "last_release_at": None,
                "last_release_reason": None}

    def release(self, *, reason: str = "requested", force: bool = False) -> dict:
        if self._busy and not force:
            raise ChatBusy("a generation is running; release was refused")
        was = self._loaded
        self._loaded = False
        return {"released": was, "reason": reason}

    def complete(self, messages, *, max_tokens=None, temperature=0.7, top_p=0.95,
                 response_format=None, seed=None, thinking=False) -> dict:
        self._loaded = True
        return {"text": "Once upon a time.", "elapsed_seconds": 0.01,
                "max_tokens": max_tokens or self.max_output_tokens,
                "prompt_tokens": 3, "json_valid": None, "repairs": 0}

    def stream(self, messages, *, max_tokens=None, temperature=0.7, top_p=0.95,
               response_format=None, seed=None, thinking=False):
        self._loaded = True
        for piece in ("Once ", "upon ", "a time."):
            yield piece

    def count_tokens(self, text: str):
        return len(text.split())

    def prompt_length(self, messages):
        return 3


class MemoryPolicyTests(unittest.TestCase):
    def test_default_mode_is_immediate_and_shape_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = memory_policy_module.MemoryPolicy(Path(tmp), _FakeEngine())
            status = policy.status()
            self.assertEqual(status["mode"], "immediate")
            self.assertEqual(status["default_mode"], "immediate")
            self.assertEqual({option["mode"] for option in status["options"]},
                             {"performance", "balanced", "memory_saver", "immediate"})
            self.assertIn("resident", status)
            self.assertIn("machine_bytes", status)

    def test_invalid_mode_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = memory_policy_module.MemoryPolicy(Path(tmp), _FakeEngine())
            with self.assertRaises(ValueError):
                policy.save("turbo")

    def test_release_refuses_while_generating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _FakeEngine()
            engine._busy = True
            policy = memory_policy_module.MemoryPolicy(Path(tmp), engine)
            with self.assertRaises(ChatBusy):
                policy.release("manual")

    def test_immediate_mode_releases_an_idle_loaded_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _FakeEngine()
            engine._loaded = True
            engine._last_activity = time.time() - 10
            policy = memory_policy_module.MemoryPolicy(Path(tmp), engine)
            policy.tick()
            self.assertFalse(engine.loaded())
            self.assertEqual(policy.status()["last_release_reason"], "idle")
            self.assertEqual(policy.status()["release_count"], 1)

    def test_performance_mode_never_idle_releases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _FakeEngine()
            engine._loaded = True
            engine._last_activity = time.time() - 10_000
            policy = memory_policy_module.MemoryPolicy(Path(tmp), engine)
            policy.save("performance")
            policy.tick()
            self.assertTrue(engine.loaded())

    def test_a_busy_model_is_never_idle_released(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _FakeEngine()
            engine._loaded = True
            engine._busy = True
            engine._last_activity = time.time() - 10
            policy = memory_policy_module.MemoryPolicy(Path(tmp), engine)
            policy.tick()
            self.assertTrue(engine.loaded())


class ChatApiTests(unittest.TestCase):
    def _client(self, engine: _FakeEngine) -> TestClient:
        with tempfile.TemporaryDirectory() as tmp:
            policy = memory_policy_module.MemoryPolicy(Path(tmp), engine)
            app = create_app(engine, policy, TOKEN, "a" * 64)
        return TestClient(app)

    def test_owner_credential_required(self) -> None:
        client = self._client(_FakeEngine())
        response = client.get("/api/health")
        self.assertEqual(response.status_code, 401)

    def test_the_owner_gate_answers_before_the_body_or_the_route(self) -> None:
        # The gate is a pass-through ASGI middleware now (BaseHTTPMiddleware swallowed the
        # stream disconnect), so pin the behaviour it must keep: 401 for every path, before the
        # body is parsed and before any route is matched.
        client = self._client(_FakeEngine(verified=True))
        self.assertEqual(client.get("/api/health").status_code, 401)
        self.assertEqual(
            client.post("/definitely/not/a/route",
                        headers={"x-studio-token": "wrong"}).status_code, 401)
        self.assertEqual(
            client.post("/v1/chat/completions",
                        headers={"x-studio-token": "wrong",
                                 "content-type": "application/json"},
                        content=b"{not json").status_code, 401)

    def test_health_and_catalog_report_chat_modality(self) -> None:
        client = self._client(_FakeEngine(verified=True))
        headers = {"x-studio-token": TOKEN}
        health = client.get("/api/health", headers=headers)
        self.assertEqual(health.status_code, 200)
        catalog = client.get("/api/catalog", headers=headers).json()["models"][0]
        self.assertEqual(catalog["modality"], "chat")
        self.assertEqual(catalog["hub_operation"], "chat.completion")
        self.assertEqual(catalog["context_window_tokens"], 262144)

    def test_completion_refused_until_model_is_installed(self) -> None:
        client = self._client(_FakeEngine(verified=False))
        response = client.post("/v1/chat/completions", headers={"x-studio-token": TOKEN},
                               json={"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(response.status_code, 409)

    def test_non_streaming_completion_shape(self) -> None:
        client = self._client(_FakeEngine(verified=True))
        response = client.post("/v1/chat/completions", headers={"x-studio-token": TOKEN},
                               json={"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["choices"][0]["message"]["content"], "Once upon a time.")
        self.assertEqual(body["usage"]["prompt_tokens"], 3)

    def test_streaming_completion_emits_deltas_and_done(self) -> None:
        client = self._client(_FakeEngine(verified=True))
        with client.stream("POST", "/v1/chat/completions",
                           headers={"x-studio-token": TOKEN},
                           json={"messages": [{"role": "user", "content": "hi"}],
                                 "stream": True}) as response:
            body = "".join(response.iter_text())
        self.assertIn("chat.completion.chunk", body)
        self.assertTrue(body.rstrip().endswith("data: [DONE]"))
        self.assertIn("Once ", body)

    def test_openai_model_listing_diagnostics_and_chat_models(self) -> None:
        client = self._client(_FakeEngine(verified=True))
        headers = {"x-studio-token": TOKEN}
        listing = client.get("/v1/models", headers=headers).json()
        self.assertEqual(listing["object"], "list")
        self.assertEqual(listing["data"][0]["id"], _FakeEngine.model_id)
        self.assertTrue(client.get("/api/chat/models", headers=headers).json()["models"][0]["cached"])
        diagnostics = client.get("/api/chat/diagnostics", headers=headers).json()
        self.assertEqual(diagnostics["engine"], "mlx-lm")
        self.assertEqual(diagnostics["context_window_tokens"], 262144)

    def test_memory_policy_round_trip(self) -> None:
        client = self._client(_FakeEngine(verified=True))
        headers = {"x-studio-token": TOKEN}
        saved = client.put("/api/memory-policy", headers=headers, json={"mode": "balanced"})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["mode"], "balanced")
        refused = client.put("/api/memory-policy", headers=headers, json={"mode": "nope"})
        self.assertEqual(refused.status_code, 422)


class _RecordingEngine:
    """Records what the route passed to the engine; generates nothing.

    Shaped like the shipped engine's published surface, without weights. `check_prompt_fits` is
    deliberately absent: these tests are about the route's own refusals, and a double that
    measured prompts would refuse on its own behalf.
    """

    model_id = "mlx-community/Qwen3.5-9B-4bit"
    revision = REVISION
    label = "Qwen3.5-9B-4bit"
    family = "qwen3_5"
    context_window_tokens = 262144
    max_output_tokens = 4096
    minimum_unified_memory_gb = 16.0

    def __init__(self, *, verified: bool = True) -> None:
        self._verified = verified
        self.seen: list[dict] = []

    def preflight(self) -> dict:
        return {"state": "files_verified" if self._verified else "missing"}

    def loaded(self) -> bool:
        return False

    def last_activity_at(self):
        return None

    def status(self) -> dict:
        return {"model_id": self.model_id, "revision": self.revision, "loaded": False,
                "busy": False, "loaded_at": None, "active_memory_bytes": None,
                "peak_memory_bytes": None, "context_window_tokens": self.context_window_tokens,
                "max_output_tokens": self.max_output_tokens, "last_release_at": None,
                "last_release_reason": None, "streams_started": 0, "streams_finished": 0}

    def model_limits(self) -> dict:
        return {
            "context_window_tokens": {
                "value": 262144,
                "source": "model/config.json text_config.max_position_embeddings"},
            "max_output_tokens": {
                "value": 4096,
                "source": "worker default: the checkpoint ships no generation_config.json"},
        }

    def count_tokens(self, text: str) -> int:
        return 1

    def prompt_length(self, messages) -> int:
        return 1

    def complete(self, messages, **kwargs) -> dict:
        self.seen.append(kwargs)
        return {"text": "ok", "elapsed_seconds": 0.01,
                "max_tokens": kwargs.get("max_tokens") or self.max_output_tokens,
                "prompt_tokens": 1, "json_valid": None, "repairs": 0}

    def stream(self, messages, **kwargs):
        self.seen.append(kwargs)
        yield "ok"

    def release(self, **kwargs) -> dict:
        return {"released": False}


class ChatParameterSurfaceTests(unittest.TestCase):
    """The route refuses what the lane does not honour, and names the field.

    Every case here was measured as accepted — and silently dropped, or accepted with the
    opposite meaning — by the 2026-09-25 review against the shipped 0.62 worker. The point of
    each assertion is that the *refusal* names the field, because a parameter accepted and then
    discarded is a lie the caller cannot see.
    """

    MESSAGE = {"messages": [{"role": "user", "content": "hi"}]}

    def _client(self, engine=None):
        engine = engine or _RecordingEngine()
        with tempfile.TemporaryDirectory() as tmp:
            policy = memory_policy_module.MemoryPolicy(Path(tmp), engine)
            app = create_app(engine, policy, TOKEN, "a" * 64)
        return TestClient(app), engine

    def _post(self, client, body, **kwargs):
        return client.post("/v1/chat/completions", headers={"x-studio-token": TOKEN},
                           json=body, **kwargs)

    def test_thinking_accepts_only_a_real_json_boolean(self) -> None:
        client, engine = self._client()
        for value in ("false", "no", "0", 0, 1, None, [], {}):
            response = self._post(client, {**self.MESSAGE, "thinking": value})
            self.assertEqual(response.status_code, 422,
                             f"thinking={value!r} was accepted; it must be refused")
            detail = response.json()["detail"]
            self.assertIn("thinking", detail)
            self.assertIn(type(value).__name__, detail,
                          "the refusal does not say what type was received")
            self.assertIn(repr(value), detail,
                          "the refusal does not say what value was received")
        self.assertEqual(engine.seen, [], "a refused request must never reach the engine")

    def test_thinking_reaches_the_engine_as_the_boolean_the_caller_sent(self) -> None:
        # The old code did `bool(body.get("thinking", False))`, so the string "false" turned
        # thinking ON. These three are the contract: a real boolean, in both directions.
        client, engine = self._client()
        for sent, expected in ((True, True), (False, False), (None, False)):
            body = dict(self.MESSAGE)
            if sent is not None:
                body["thinking"] = sent
            engine.seen = []
            response = self._post(client, body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIs(engine.seen[-1]["thinking"], expected)

    def test_stream_accepts_only_a_real_json_boolean(self) -> None:
        # Same class of defect as `thinking`: a truthy "false" would start an SSE response for a
        # caller that asked for one JSON completion.
        client, engine = self._client()
        for value in ("false", "true", 0, 1):
            response = self._post(client, {**self.MESSAGE, "stream": value})
            self.assertEqual(response.status_code, 422, f"stream={value!r} was accepted")
            self.assertIn("stream", response.json()["detail"])
        self.assertEqual(engine.seen, [])

    def test_a_non_finite_temperature_is_refused_by_name(self) -> None:
        # Python's JSON parser accepts the bare tokens NaN/Infinity, so they have to be caught
        # after parsing: the old route passed NaN through to `make_sampler(temp=nan)`.
        client, engine = self._client()
        for token in ("NaN", "Infinity", "-Infinity"):
            raw = ('{"messages":[{"role":"user","content":"hi"}],"temperature":%s}' % token)
            response = client.post(
                "/v1/chat/completions",
                headers={"x-studio-token": TOKEN, "content-type": "application/json"},
                content=raw)
            self.assertEqual(response.status_code, 422, f"temperature:{token} was accepted")
            detail = response.json()["detail"]
            self.assertIn("temperature", detail)
            self.assertIn("finite", detail)
        self.assertEqual(engine.seen, [], "a non-finite number reached the engine")

    def test_temperature_is_bounded_where_a_bound_is_published(self) -> None:
        client, engine = self._client()
        self.assertEqual(self._post(client, {**self.MESSAGE, "temperature": -0.1}).status_code,
                         422)
        # Nothing states a maximum, so no maximum is invented: a high-but-finite temperature is
        # served, not refused on a number this worker made up.
        engine.seen = []
        self.assertEqual(self._post(client, {**self.MESSAGE, "temperature": 1.5}).status_code, 200)
        self.assertEqual(engine.seen[-1]["temperature"], 1.5)

    def test_top_p_is_bounded_exactly_as_the_contract_publishes(self) -> None:
        client, engine = self._client()
        for value in (0, 0.0, -0.5, 1.5):
            response = self._post(client, {**self.MESSAGE, "top_p": value})
            self.assertEqual(response.status_code, 422, f"top_p={value!r} was accepted")
            self.assertIn("top_p", response.json()["detail"])
        engine.seen = []
        self.assertEqual(self._post(client, {**self.MESSAGE, "top_p": 1.0}).status_code, 200)
        self.assertEqual(engine.seen[-1]["top_p"], 1.0)

    def test_max_tokens_is_bounded_by_the_maximum_the_lane_publishes(self) -> None:
        client, engine = self._client()
        response = self._post(client, {**self.MESSAGE, "max_tokens": 100_000_000_000})
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertIn("100000000000", detail, "the refusal does not name the value received")
        self.assertIn("4096", detail, "the refusal does not name the published maximum")
        self.assertEqual(self._post(client, {**self.MESSAGE, "max_tokens": 4097}).status_code, 422)
        engine.seen = []
        self.assertEqual(self._post(client, {**self.MESSAGE, "max_tokens": 4096}).status_code, 200)
        self.assertEqual(engine.seen[-1]["max_tokens"], 4096)
        self.assertEqual(
            self._post(client, {**self.MESSAGE, "max_completion_tokens": 4096}).status_code, 200)

    def test_a_seed_that_is_not_an_integer_is_refused_rather_than_dropped(self) -> None:
        client, engine = self._client()
        for value in ("not-a-number", 42.5, True, [42]):
            response = self._post(client, {**self.MESSAGE, "seed": value})
            self.assertEqual(response.status_code, 422, f"seed={value!r} was accepted")
            detail = response.json()["detail"]
            self.assertIn("seed", detail)
            self.assertIn(repr(value), detail)
        engine.seen = []
        self.assertEqual(self._post(client, {**self.MESSAGE, "seed": 42}).status_code, 200)
        self.assertEqual(engine.seen[-1]["seed"], 42)

    def test_parameters_this_worker_does_not_honour_are_refused_by_name(self) -> None:
        client, engine = self._client()
        for name, value in (("tools", [{"type": "function"}]),
                            ("n", 3),
                            ("logit_bias", {"1": 1}),
                            ("frequency_penalty", 0.5),
                            ("stop", ["\n"])):
            response = self._post(client, {**self.MESSAGE, name: value})
            self.assertEqual(response.status_code, 422, f"{name} was silently discarded")
            detail = response.json()["detail"]
            self.assertIn(name, detail)
            self.assertIn("not supported by this worker", detail)
        self.assertEqual(engine.seen, [], "a refused request must never reach the engine")

    def test_all_of_them_together_are_named_in_one_refusal(self) -> None:
        client, _ = self._client()
        response = self._post(client, {**self.MESSAGE, "tools": [1], "n": 5,
                                       "logit_bias": {"a": 1}})
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        for name in ("tools", "n", "logit_bias"):
            self.assertIn(name, detail)


class _CountedTokenizer:
    """A tokenizer that reports a fixed token count, so a context bound can be exercised."""

    def __init__(self, count: int) -> None:
        self._count = count

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True,
                            enable_thinking=None):
        return "<prompt>"

    def encode(self, text: str):
        return _TokenCount(self._count)


class _TokenCount:
    """Only `len()` is read, so a count can be promised without encoding anything."""

    def __init__(self, count: int) -> None:
        self._count = count

    def __len__(self) -> int:
        return self._count


class PromptBudgetTests(unittest.TestCase):
    """A prompt past the model's own context is refused by name, never truncated.

    The bound is the model's, not this worker's: `model/config.json
    text_config.max_position_embeddings` for the shipped Qwen3.5-9B 4-bit states 262144. Where
    nothing states a context window, nothing is refused.
    """

    MESSAGE = [{"role": "user", "content": "hi"}]

    def _engine(self, root: Path, *, context: int | None, tokens: int) -> ChatEngine:
        model_dir = root / "model"
        model_dir.mkdir(parents=True)
        config = {} if context is None else {"text_config": {"max_position_embeddings": context}}
        (model_dir / "config.json").write_text(json.dumps(config))
        (model_dir / "model.safetensors").write_bytes(b"weights")
        (model_dir / "tokenizer.json").write_text("{}")
        return ChatEngine(model_dir=model_dir, model_id="mlx-community/Qwen3.5-9B-4bit",
                          revision="r" * 40)

    def test_a_prompt_that_cannot_fit_is_refused_with_the_measured_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp), context=100, tokens=90)
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _CountedTokenizer(90))), \
                    mock.patch("mlx_lm.generate", lambda *a, **k: "never reached"):
                with self.assertRaises(ChatContextExceeded) as caught:
                    engine.complete(self.MESSAGE, max_tokens=20)
        detail = str(caught.exception)
        self.assertIn("messages", detail, "the refusal does not name the field")
        self.assertIn("90", detail, "the refusal does not name the measured prompt tokens")
        self.assertIn("20", detail, "the refusal does not name the asked-for output budget")
        self.assertIn("100", detail, "the refusal does not name the model's stated context")
        self.assertIn("model/config.json text_config.max_position_embeddings", detail,
                      "the refusal does not name where the limit comes from")

    def test_a_prompt_that_fits_exactly_is_served(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp), context=100, tokens=80)
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _CountedTokenizer(80))), \
                    mock.patch("mlx_lm.generate", lambda *a, **k: "served"):
                outcome = engine.complete(self.MESSAGE, max_tokens=20)
        self.assertEqual(outcome["text"], "served")

    def test_nothing_is_refused_when_nothing_states_a_context_window(self) -> None:
        # 4 million tokens with no stated window: the worker must not invent a limit.
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp), context=None, tokens=4_000_000)
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _CountedTokenizer(4_000_000))), \
                    mock.patch("mlx_lm.generate", lambda *a, **k: "served"):
                outcome = engine.complete(self.MESSAGE, max_tokens=20)
        self.assertEqual(outcome["text"], "served")
        self.assertIsNone(engine.context_window_tokens)

    def test_the_route_refuses_a_prompt_past_the_context_before_streaming(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp), context=100, tokens=90)
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _CountedTokenizer(90))):
                policy = memory_policy_module.MemoryPolicy(Path(tmp), engine)
                client = TestClient(create_app(engine, policy, TOKEN, "a" * 64))
                for stream in (False, True):
                    response = client.post(
                        "/v1/chat/completions", headers={"x-studio-token": TOKEN},
                        json={"messages": self.MESSAGE, "stream": stream})
                    self.assertEqual(response.status_code, 422,
                                     f"stream={stream} did not refuse a prompt past the context")
                    detail = response.json()["detail"]
                    self.assertIn("messages", detail)
                    self.assertIn("100", detail)


class _FakeTokenizer:
    def __init__(self, template: str | None = None) -> None:
        self._template = template

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        return self._template

    def encode(self, text: str):
        return list(range(len(text.split())))


class PromptBuildingTests(unittest.TestCase):
    def _engine(self) -> ChatEngine:
        return ChatEngine(model_dir=Path("/nonexistent"), model_id="m", revision="r")

    def test_uses_the_model_chat_template_when_present(self) -> None:
        engine = self._engine()
        engine._tokenizer = _FakeTokenizer("<|user|>hi<|assistant|>")
        self.assertEqual(engine._prompt([{"role": "user", "content": "hi"}]),
                         "<|user|>hi<|assistant|>")

    def test_thinking_is_off_by_default_and_can_be_requested(self) -> None:
        # Qwen3.5-9B emits a long "Thinking Process:" trace (and breaks JSON mode) unless the
        # template is told not to think. Thinking is off unless a caller asks for it.
        class ThinkingTokenizer:
            def __init__(self) -> None:
                self.calls: list[bool] = []

            def apply_chat_template(self, messages, tokenize=False,
                                    add_generation_prompt=True, enable_thinking=None):
                self.calls.append(enable_thinking)
                return "<prompt>"

            def encode(self, text):
                return []

        engine = self._engine()
        tokenizer = ThinkingTokenizer()
        engine._tokenizer = tokenizer
        engine._prompt([{"role": "user", "content": "hi"}])
        self.assertIs(tokenizer.calls[-1], False,
                      "a chat model must not emit a Thinking Process trace by default")
        engine._prompt([{"role": "user", "content": "hi"}], thinking=True)
        self.assertIs(tokenizer.calls[-1], True)

    def test_falls_back_when_the_tokenizer_has_no_template(self) -> None:
        class NoTemplate(_FakeTokenizer):
            apply_chat_template = None

        engine = self._engine()
        engine._tokenizer = NoTemplate()
        prompt = engine._prompt([{"role": "user", "content": "hello world"}])
        self.assertIn("user: hello world", prompt)
        self.assertTrue(prompt.endswith("assistant:"))

    def test_token_counts_are_none_without_a_loaded_model(self) -> None:
        engine = self._engine()
        self.assertIsNone(engine.count_tokens("hi"))
        self.assertIsNone(engine.prompt_length([{"role": "user", "content": "hi"}]))

    def test_completion_passes_a_sampler_not_raw_sampling_kwargs(self) -> None:
        # mlx-lm 0.31's generate_step takes `sampler`; passing temp/top_p raises TypeError.
        engine = self._engine()
        engine._model = object()
        engine._tokenizer = _FakeTokenizer("<|user|>hi<|assistant|>")
        captured: dict = {}

        def fake_generate(model, tokenizer, prompt, **kwargs):
            captured.update(kwargs)
            return "a reply"

        with mock.patch("mlx_lm.generate", fake_generate):
            outcome = engine.complete([{"role": "user", "content": "hi"}],
                                      max_tokens=16, temperature=0.5, top_p=0.9)
        self.assertIn("sampler", captured)
        self.assertNotIn("temp", captured)
        self.assertNotIn("top_p", captured)
        self.assertEqual(outcome["text"], "a reply")
        self.assertEqual(outcome["max_tokens"], 16)

    def test_streaming_passes_a_sampler_not_raw_sampling_kwargs(self) -> None:
        engine = self._engine()
        engine._model = object()
        engine._tokenizer = _FakeTokenizer("<|user|>hi<|assistant|>")
        captured: dict = {}

        def fake_stream(model, tokenizer, prompt, **kwargs):
            captured.update(kwargs)
            yield type("R", (), {"text": "chunk"})()

        with mock.patch("mlx_lm.stream_generate", fake_stream):
            chunks = list(engine.stream([{"role": "user", "content": "hi"}], max_tokens=8))
        self.assertIn("sampler", captured)
        self.assertNotIn("temp", captured)
        self.assertEqual(chunks, ["chunk"])


class _StubTokenizer:
    """A tokenizer shaped like the real one, without needing a model."""

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        return "<|user|>" + messages[-1]["content"] + "<|assistant|>"

    def encode(self, text):
        return text.split()


class RealEngineIntegrationTests(unittest.TestCase):
    """The real engine through the real app, with only `mlx_lm` stubbed.

    The rest of the suite uses a `_FakeEngine`, which cannot catch a mistake in the engine's
    own load/prompt/sampler/usage path. This drives that path end to end.
    """

    def _model_dir(self, root: Path) -> Path:
        model_dir = root / "model"
        model_dir.mkdir()
        # 8192, not 4096: the request below asks for the worker's default output budget, and a
        # prompt plus that budget has to fit the context the model states.
        (model_dir / "config.json").write_text(
            json.dumps({"text_config": {"max_position_embeddings": 8192}}))
        (model_dir / "model.safetensors").write_bytes(b"weights")
        (model_dir / "tokenizer.json").write_text("{}")
        return model_dir

    def test_completion_flows_through_the_real_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = ChatEngine(model_dir=self._model_dir(Path(tmp)),
                                model_id="mlx-community/Qwen3.5-9B-4bit", revision="a" * 40)
            captured: dict = {}

            def fake_generate(model, tokenizer, prompt, **kwargs):
                captured["prompt"] = prompt
                captured["kwargs"] = kwargs
                return "hello there"

            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _StubTokenizer())), \
                    mock.patch("mlx_lm.generate", fake_generate):
                policy = memory_policy_module.MemoryPolicy(Path(tmp), engine)
                app = create_app(engine, policy, TOKEN, "b" * 64)
                with TestClient(app) as client:
                    response = client.post(
                        "/v1/chat/completions", headers={"x-studio-token": TOKEN},
                        json={"messages": [{"role": "user", "content": "hi"}]})

            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["choices"][0]["message"]["content"], "hello there")
            self.assertEqual(captured["prompt"], "<|user|>hi<|assistant|>")
            self.assertIn("sampler", captured["kwargs"])
            self.assertNotIn("temp", captured["kwargs"])
            self.assertEqual(body["usage"], {"prompt_tokens": 1, "completion_tokens": 2,
                                             "total_tokens": 3})
            self.assertEqual(engine.context_window_tokens, 8192)

    def test_engine_from_environment_resolves_the_library_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp) / "models"
            (models / "qwen3-5-9b-4bit" / ("c" * 40) / "model").mkdir(parents=True)
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps({
                "package_id": "qwen3-5-9b-4bit", "revision": "c" * 40,
                "model_id": "mlx-community/Qwen3.5-9B-4bit",
                "qualification": {"context_window_tokens": 8192},
            }))
            with mock.patch.dict(os.environ, {
                    "STUDIOHUB_MODELS_DIR": str(models),
                    "STUDIOHUB_CHAT_MANIFEST": str(manifest)}):
                engine = engine_from_environment()
        self.assertEqual(engine.model_dir, models / "qwen3-5-9b-4bit" / ("c" * 40) / "model")
        self.assertEqual(engine.context_window_tokens, 8192)
        self.assertEqual(engine.max_output_tokens, 4096)


class StreamLockTests(unittest.TestCase):
    """F4: however a stream ends, the model lock comes back and the counters balance.

    Reproduced on the shipped 0.62 worker before the fix: a client that disconnected mid-SSE
    left `lock.locked()=True` with `streams started=1 finished=0` twenty seconds later (a full
    stream took six), so the next `complete()` blocked. The counters are the engine's own, and
    a starting/finishing mismatch is exactly that wedge.
    """

    def _engine(self, root: Path) -> ChatEngine:
        model_dir = root / "model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text(
            json.dumps({"text_config": {"max_position_embeddings": 262144}}))
        (model_dir / "model.safetensors").write_bytes(b"weights")
        (model_dir / "tokenizer.json").write_text("{}")
        return ChatEngine(model_dir=model_dir, model_id="mlx-community/Qwen3.5-9B-4bit",
                          revision="r" * 40)

    @staticmethod
    def _stream_generate(chunks: int = 4):
        def fake(model, tokenizer, prompt, **kwargs):
            for index in range(chunks):
                time.sleep(0.02)
                yield type("R", (), {"text": f"chunk{index} "})()
        return fake

    def _complete_within(self, engine: ChatEngine, *, seconds: float) -> dict | None:
        """Run a completion on its own thread; fail loudly instead of hanging on a held lock."""
        outcome: dict = {}

        def run() -> None:
            try:
                outcome["value"] = engine.complete(
                    [{"role": "user", "content": "hi"}], max_tokens=4)
            except BaseException as exc:  # pragma: no cover - surfaced by the caller
                outcome["error"] = exc

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(timeout=seconds)
        self.assertFalse(worker.is_alive(),
                         "complete() blocked: the model lock was never released")
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("value")

    def test_an_abandoned_stream_releases_the_lock_and_balances_the_counters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _TemplateTokenizer())), \
                    mock.patch("mlx_lm.stream_generate", self._stream_generate()):
                stream = engine.stream([{"role": "user", "content": "hi"}], max_tokens=8)
                self.assertEqual(next(stream), "chunk0 ")
                self.assertTrue(engine.status()["busy"], "a live stream must report busy")
                stream.close()
                stream.close()  # idempotent: a second close must not release a second time
            status = engine.status()
            self.assertEqual((status["streams_started"], status["streams_finished"]), (1, 1))
            self.assertFalse(status["busy"])
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _TemplateTokenizer())), \
                    mock.patch("mlx_lm.generate", lambda *a, **k: "served"):
                outcome = self._complete_within(engine, seconds=5)
        self.assertEqual(outcome["text"], "served")
        self.assertEqual(engine.status()["streams_finished"], 1,
                         "the completion must not be counted as a stream")

    def test_a_completed_stream_balances_and_frees_the_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _TemplateTokenizer())), \
                    mock.patch("mlx_lm.stream_generate", self._stream_generate()):
                chunks = list(engine.stream([{"role": "user", "content": "hi"}], max_tokens=8))
            self.assertEqual(chunks, ["chunk0 ", "chunk1 ", "chunk2 ", "chunk3 "])
            status = engine.status()
            self.assertEqual((status["streams_started"], status["streams_finished"]), (1, 1))
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _TemplateTokenizer())), \
                    mock.patch("mlx_lm.generate", lambda *a, **k: "served"):
                self.assertIsNotNone(self._complete_within(engine, seconds=5))

    def test_an_errored_stream_balances_and_frees_the_lock(self) -> None:
        def exploding(model, tokenizer, prompt, **kwargs):
            yield type("R", (), {"text": "half "})()
            raise RuntimeError("mlx failed mid-stream")

        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _TemplateTokenizer())), \
                    mock.patch("mlx_lm.stream_generate", exploding):
                with self.assertRaises(RuntimeError):
                    list(engine.stream([{"role": "user", "content": "hi"}], max_tokens=8))
            status = engine.status()
            self.assertEqual((status["streams_started"], status["streams_finished"]), (1, 1))
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _TemplateTokenizer())), \
                    mock.patch("mlx_lm.generate", lambda *a, **k: "served"):
                self.assertIsNotNone(self._complete_within(engine, seconds=5))

    def test_a_stream_that_never_started_takes_and_releases_nothing(self) -> None:
        # Closing a generator that was never resumed must not release a lock this request never
        # took: the acquire happens inside the generator body, on the first `next()`.
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            stream = engine.stream([{"role": "user", "content": "hi"}], max_tokens=8)
            stream.close()
            status = engine.status()
            self.assertEqual((status["streams_started"], status["streams_finished"]), (0, 0))
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _TemplateTokenizer())), \
                    mock.patch("mlx_lm.generate", lambda *a, **k: "served"):
                self.assertIsNotNone(self._complete_within(engine, seconds=5))
            self.assertEqual(engine.status()["streams_started"], 0)

    def test_a_second_stream_waits_and_each_release_is_its_own(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _TemplateTokenizer())), \
                    mock.patch("mlx_lm.stream_generate", self._stream_generate()):
                first = engine.stream([{"role": "user", "content": "hi"}], max_tokens=8)
                self.assertEqual(next(first), "chunk0 ")
                second_chunks: list[str] = []

                def consume_second() -> None:
                    second_chunks.extend(
                        engine.stream([{"role": "user", "content": "hi"}], max_tokens=8))

                worker = threading.Thread(target=consume_second, daemon=True)
                worker.start()
                time.sleep(0.3)
                self.assertTrue(worker.is_alive(),
                                "the second stream must wait for the first to finish")
                self.assertEqual(engine.status()["streams_started"], 1,
                                 "the waiting stream must not count as started")
                self.assertEqual(engine.status()["streams_finished"], 0)
                first.close()
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(second_chunks, ["chunk0 ", "chunk1 ", "chunk2 ", "chunk3 "])
            status = engine.status()
            self.assertEqual((status["streams_started"], status["streams_finished"]), (2, 2),
                             "the two streams did not each release their own hold exactly once")


class StreamDisconnectRouteTests(unittest.TestCase):
    """The measured F4 reproduction, against real uvicorn and the real route.

    The client reads one chunk of a stream that would run for ~4 s and goes away. Before the
    fix, twenty seconds later the engine still held `started=1 finished=0` and the next
    `complete()` never returned.
    """

    def _engine(self, root: Path) -> ChatEngine:
        model_dir = root / "model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text(
            json.dumps({"text_config": {"max_position_embeddings": 262144}}))
        (model_dir / "model.safetensors").write_bytes(b"weights")
        (model_dir / "tokenizer.json").write_text("{}")
        return ChatEngine(model_dir=model_dir, model_id="mlx-community/Qwen3.5-9B-4bit",
                          revision="r" * 40)

    @staticmethod
    def _stream_generate(chunks: int = 40, delay: float = 0.1):
        def fake(model, tokenizer, prompt, **kwargs):
            for index in range(chunks):
                time.sleep(delay)
                yield type("R", (), {"text": f"chunk{index:02d} "})()
        return fake

    def test_a_client_that_disconnects_mid_stream_does_not_wedge_the_model_lock(self) -> None:
        try:
            import httpx
            import uvicorn
        except ImportError as exc:  # pragma: no cover - the app runtime ships both
            self.skipTest(f"uvicorn/httpx unavailable: {exc}")

        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            policy = memory_policy_module.MemoryPolicy(Path(tmp), engine)
            app = create_app(engine, policy, TOKEN, "a" * 64)
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0,
                                                   log_level="warning"))
            thread = threading.Thread(target=server.run, daemon=True)
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _TemplateTokenizer())), \
                    mock.patch("mlx_lm.stream_generate",
                               self._stream_generate(chunks=40, delay=0.1)), \
                    mock.patch("mlx_lm.generate", lambda *a, **k: "served"):
                thread.start()
                try:
                    for _ in range(200):
                        if server.started:
                            break
                        time.sleep(0.05)
                    else:  # pragma: no cover - a server that never starts is a broken host
                        self.skipTest("uvicorn did not start")
                    port = server.servers[0].sockets[0].getsockname()[1]
                    url = f"http://127.0.0.1:{port}/v1/chat/completions"
                    headers = {"x-studio-token": TOKEN}
                    with httpx.stream("POST", url, headers=headers, timeout=10.0,
                                      json={"messages": [{"role": "user", "content": "hi"}],
                                            "stream": True}) as response:
                        for line in response.iter_lines():
                            if line:
                                break
                    # The client is gone. The stream would have taken 4 s; the lock must come
                    # back without waiting for it.
                    deadline = time.time() + 10.0
                    while time.time() < deadline and engine.status()["streams_finished"] < 1:
                        time.sleep(0.05)
                    status = engine.status()
                    self.assertEqual(status["streams_started"], 1)
                    self.assertEqual(status["streams_finished"], 1,
                                     "the abandoned stream never finished: the model lock is held")
                    # The activity contract says cancelled, not done: nothing was delivered.
                    activity = httpx.get(f"http://127.0.0.1:{port}/api/fleet/activity",
                                         headers=headers, timeout=5.0).json()
                    self.assertEqual(activity["latest"]["state"], "cancelled")
                    # And the next request is actually served rather than blocked forever.
                    try:
                        second = httpx.post(url, headers=headers, timeout=10.0,
                                            json={"messages": [{"role": "user",
                                                                "content": "again"}]})
                    except httpx.TimeoutException:
                        self.fail("the next completion blocked: the abandoned stream still "
                                  "holds the model lock")
                    self.assertEqual(second.status_code, 200, second.text)
                    self.assertEqual(second.json()["choices"][0]["message"]["content"], "served")
                finally:
                    server.should_exit = True
                    thread.join(timeout=10)


class _TemplateTokenizer:
    """Renders every message (including a system instruction) so injection is observable."""

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        return "".join(f"<|{m['role']}|>{m['content']}" for m in messages) + "<|assistant|>"

    def encode(self, text):
        return text.split()


class JsonModeTests(unittest.TestCase):
    def test_json_instruction_is_built_for_object_and_schema(self) -> None:
        self.assertIn("only one valid JSON object",
                      ChatEngine.json_instruction({"type": "json_object"}))
        schema = {"type": "object", "properties": {"title": {"type": "string"}}}
        schema_text = ChatEngine.json_instruction({"type": "json_schema", "json_schema": schema})
        self.assertIn("JSON Schema", schema_text)
        self.assertIn("title", schema_text)
        self.assertIsNone(ChatEngine.json_instruction(None))
        self.assertIsNone(ChatEngine.json_instruction({"type": "text"}))

    def test_json_object_repairs_once_and_reports_validity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text("{}")
            (model_dir / "model.safetensors").write_bytes(b"weights")
            (model_dir / "tokenizer.json").write_text("{}")
            engine = ChatEngine(model_dir=model_dir, model_id="m", revision="r" * 40)
            replies = iter(["Here you go:\n```json\n{\"a\": 1}\n```", '{"a": 1}'])
            prompts: list[str] = []

            def fake_generate(model, tokenizer, prompt, **kwargs):
                prompts.append(prompt)
                return next(replies)

            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _TemplateTokenizer())), \
                    mock.patch("mlx_lm.generate", fake_generate):
                outcome = engine.complete([{"role": "user", "content": "give me json"}],
                                          response_format={"type": "json_object"})

        self.assertEqual(len(prompts), 2, "a JSON failure must trigger exactly one repair")
        self.assertTrue(outcome["json_valid"])
        self.assertEqual(outcome["repairs"], 1)
        self.assertEqual(outcome["text"], '{"a": 1}')
        self.assertIn("<|system|>Return only one valid JSON object", prompts[0])
        self.assertIn("not valid JSON", prompts[1])

    def test_seed_is_applied_when_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text("{}")
            (model_dir / "model.safetensors").write_bytes(b"weights")
            (model_dir / "tokenizer.json").write_text("{}")
            engine = ChatEngine(model_dir=model_dir, model_id="m", revision="r" * 40)
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _TemplateTokenizer())), \
                    mock.patch("mlx_lm.generate", lambda *a, **k: "ok"), \
                    mock.patch("mlx.core.random.seed") as seed_call:
                engine.complete([{"role": "user", "content": "hi"}], seed=123)
            seed_call.assert_called_once_with(123)

    def test_without_response_format_there_is_no_instruction_or_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text("{}")
            (model_dir / "model.safetensors").write_bytes(b"weights")
            (model_dir / "tokenizer.json").write_text("{}")
            engine = ChatEngine(model_dir=model_dir, model_id="m", revision="r" * 40)
            prompts: list[str] = []

            def fake_generate(model, tokenizer, prompt, **kwargs):
                prompts.append(prompt)
                return "plain text"

            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _TemplateTokenizer())), \
                    mock.patch("mlx_lm.generate", fake_generate):
                outcome = engine.complete([{"role": "user", "content": "hi"}])

        self.assertEqual(len(prompts), 1)
        self.assertIsNone(outcome["json_valid"])
        self.assertEqual(outcome["repairs"], 0)
        self.assertNotIn("JSON", prompts[0])


class ChatLaneContractTests(unittest.TestCase):
    """The Chat lane publishes its own facts: label, family, limits, contract and activity.

    Every value here is the *model's* or the *worker's* own, with its provenance. The catalog row
    used to hardcode `"label": "Qwen3.5-9B"` (no quantization) and `"family": "qwen3.5"`, so a
    second chat model would have been published under the first one's name, and the activity
    route answered `{"active": None, "latest": None}` unconditionally, so Chat work appeared in
    no activity view anywhere.
    """

    MODEL_ID = "mlx-community/Qwen3.5-9B-4bit"
    REVISION = "8b2b98c00a6b4d291155e4890773ca8f769aee53"

    def _engine(self, root: Path, *, model_id: str | None = None,
                manifest_extra: dict | None = None, generation: dict | None = None,
                context_key: bool = True) -> ChatEngine:
        model_dir = root / "model"
        model_dir.mkdir(parents=True, exist_ok=True)
        config: dict = {"model_type": "qwen3_5"}
        if context_key:
            config["text_config"] = {"max_position_embeddings": 262144}
        (model_dir / "config.json").write_text(json.dumps(config))
        (model_dir / "model.safetensors").write_bytes(b"weights")
        (model_dir / "tokenizer.json").write_text("{}")
        if generation is not None:
            (model_dir / "generation_config.json").write_text(json.dumps(generation))
        manifest_path = root / "model-manifests" / "chat-model.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text("{}")
        manifest = {"model_id": model_id or self.MODEL_ID, **(manifest_extra or {})}
        return ChatEngine(model_dir=model_dir, model_id=manifest["model_id"],
                          revision=self.REVISION, manifest=manifest,
                          manifest_path=manifest_path)

    def _client(self, engine: ChatEngine) -> TestClient:
        with tempfile.TemporaryDirectory() as tmp:
            policy = memory_policy_module.MemoryPolicy(Path(tmp), engine)
            app = create_app(engine, policy, TOKEN, "a" * 64)
        return TestClient(app)

    def test_the_label_and_family_come_from_the_model_not_the_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            catalog = self._client(engine).get(
                "/api/catalog", headers={"x-studio-token": TOKEN}).json()["models"][0]
        self.assertEqual(catalog["label"], "Qwen3.5-9B-4bit")
        self.assertIn("4bit", catalog["label"], "the label does not name the quantization")
        self.assertEqual(catalog["family"], "qwen3_5")
        self.assertEqual(catalog["model_limits"]["context_window_tokens"], {
            "value": 262144,
            "source": "model/config.json text_config.max_position_embeddings",
        })
        self.assertEqual(catalog["model_limits"]["max_output_tokens"], {
            "value": 4096,
            "source": "worker default: the checkpoint ships no generation_config.json",
        })

    def test_a_second_model_cannot_be_published_under_the_first_models_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp), model_id="org/Other-Chat-8bit",
                                  manifest_extra={"label": "Other Chat 8-bit"})
            catalog = self._client(engine).get(
                "/api/catalog", headers={"x-studio-token": TOKEN}).json()["models"][0]
        self.assertEqual(catalog["label"], "Other Chat 8-bit")
        self.assertEqual(catalog["model_id"], "org/Other-Chat-8bit")
        self.assertNotIn("Qwen", catalog["label"])

    def test_a_manifest_that_states_a_tier_publishes_it_and_one_that_does_not_says_so(
            self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stated = self._engine(Path(tmp) / "a", manifest_extra={
                "qualification": {"minimum_unified_memory_gb": 16}})
            silent = self._engine(Path(tmp) / "b")
            published = self._client(stated).get(
                "/api/catalog", headers={"x-studio-token": TOKEN}).json()["models"][0]
            unpublished = self._client(silent).get(
                "/api/catalog", headers={"x-studio-token": TOKEN}).json()["models"][0]
        self.assertEqual(published["min_total_memory_gb"], 16.0)
        self.assertEqual(published["min_unified_memory_gb"], 16.0)
        self.assertIsNone(unpublished["min_total_memory_gb"])
        self.assertIsNone(unpublished["min_unified_memory_gb"])

    def test_a_stated_output_bound_is_read_with_its_own_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp), generation={"max_new_tokens": 8192})
            limits = self._client(engine).get(
                "/api/catalog", headers={"x-studio-token": TOKEN}).json()[
                    "models"][0]["model_limits"]
        self.assertEqual(limits["max_output_tokens"], {
            "value": 8192,
            "source": "model/generation_config.json max_new_tokens",
        })

    def test_a_context_window_nothing_states_is_null_with_the_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp), context_key=False)
            limits = self._client(engine).get(
                "/api/catalog", headers={"x-studio-token": TOKEN}).json()[
                    "models"][0]["model_limits"]
        self.assertIsNone(limits["context_window_tokens"]["value"])
        self.assertIn("nothing states a context window",
                      limits["context_window_tokens"]["source"])

    def test_the_lane_contract_is_the_chat_lane_and_omits_what_the_hub_owns(self) -> None:
        from components.hub.backend import chat_jobs

        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            contract = self._client(engine).get(
                "/api/catalog", headers={"x-studio-token": TOKEN}).json()[
                    "models"][0]["lane_contract"]
        self.assertEqual(contract["schema"], "studiohub.lane-contract.v1")
        self.assertEqual(contract["modality"], "chat")
        self.assertEqual(contract["operation"], "chat.completion")
        self.assertEqual(contract["artifact"]["media_type"], "application/json")
        self.assertEqual(contract["artifact"]["per_item"], 1)
        self.assertFalse(contract["states"]["uncertain_is_terminal"])
        self.assertFalse(contract["states"]["discard_supported"])
        self.assertTrue(contract["cancel"]["supported"])
        self.assertIn("not evidence", contract["cancel"]["semantics"])
        # The Hub owns the dispatch deadline; nothing here has measured a runtime.
        self.assertNotIn("deadline_seconds", contract)
        self.assertNotIn("runtime_seconds", contract)
        # Never offer what the lane discards.
        names = {parameter["name"] for parameter in contract["parameters"]}
        self.assertEqual(
            names & {"stop", "frequency_penalty", "presence_penalty"}, set(),
            "the contract offers a parameter the worker silently ignores",
        )
        accepted = set(chat_jobs._ALLOWED_PARAMS) - {"max_completion_tokens"}
        self.assertTrue(
            accepted <= names,
            f"the Hub accepts {sorted(accepted - names)} but the lane contract omits it",
        )

    def test_the_lane_contract_advertises_exactly_what_the_route_enforces(self) -> None:
        """F12: the published surface and the enforced surface are the same set.

        Both directions are asserted: every parameter the contract offers is honoured when sent
        with a valid value, and every field the route accepts is either offered or is one of the
        two documented aliases (the Hub's `job_id` correlation id and `max_tokens`' OpenAI
        spelling `max_completion_tokens`). A bound in the contract is then checked against the
        route that enforces it.
        """
        from components.chat.app.backend import main as chat_main

        engine = _RecordingEngine()
        with tempfile.TemporaryDirectory() as tmp:
            policy = memory_policy_module.MemoryPolicy(Path(tmp), engine)
            client = TestClient(create_app(engine, policy, TOKEN, "a" * 64))
        catalog = client.get("/api/catalog", headers={"x-studio-token": TOKEN}).json()["models"][0]
        surface = {entry["name"]: entry for entry in catalog["lane_contract"]["parameters"]}
        valid = {"model": engine.model_id, "max_tokens": 16, "temperature": 0.5, "top_p": 0.9,
                 "stream": False, "response_format": {"type": "json_object"}, "seed": 42,
                 "thinking": True}
        self.assertEqual(set(valid) | {"messages"}, set(surface),
                         "the contract offers a parameter the test does not exercise")
        for name, value in valid.items():
            engine.seen = []
            response = client.post("/v1/chat/completions", headers={"x-studio-token": TOKEN},
                                   json={"messages": [{"role": "user", "content": "hi"}],
                                         name: value})
            self.assertEqual(response.status_code, 200,
                             f"the contract advertises {name}, but the route refused it: "
                             f"{response.text}")

        # Nothing enforced goes unpublished, except the two documented aliases.
        self.assertEqual(chat_main.SUPPORTED_FIELDS,
                         set(surface) | {"job_id", "max_completion_tokens"},
                         "the route accepts a field the lane contract does not publish")
        self.assertIn("max_completion_tokens", surface["max_tokens"]["note"])

        # The published bounds are the enforced ones.
        self.assertEqual(surface["max_tokens"]["minimum"], 1)
        self.assertEqual(surface["max_tokens"]["maximum"], engine.max_output_tokens)
        self.assertEqual(surface["temperature"]["minimum"], 0.0)
        self.assertNotIn("maximum", surface["temperature"],
                         "temperature publishes a maximum this worker does not enforce")
        self.assertEqual(surface["top_p"]["minimum"], 0.0)
        self.assertTrue(surface["top_p"]["minimum_exclusive"],
                        "the contract's minimum is inclusive while the route refuses 0")
        self.assertEqual(surface["top_p"]["maximum"], 1.0)
        self.assertEqual(surface["thinking"], {**surface["thinking"], "type": "boolean",
                                               "default": False})
        self.assertEqual(surface["stream"], {**surface["stream"], "type": "boolean",
                                             "default": False})
        # The prompt bound is published from the model's own number and source, not a literal.
        self.assertIn("262144", surface["messages"]["note"])
        self.assertIn("model/config.json text_config.max_position_embeddings",
                      surface["messages"]["note"])
        # The published generation defaults are the ones the route serves with.
        self.assertEqual(catalog["generation_defaults"],
                         {"temperature": DEFAULT_TEMPERATURE, "top_p": DEFAULT_TOP_P})

    def test_the_catalog_states_packaged_inference_was_proven(self) -> None:
        # F11: the row read "pending measured proof" long after the proof existed
        # (qualification/st14-chat-2026-09-24.md), so it named a state the worker was not in.
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            qualification = self._client(engine).get(
                "/api/catalog", headers={"x-studio-token": TOKEN}).json()[
                    "models"][0]["qualification"]
        self.assertNotIn("pending", qualification["packaged_inference"])
        self.assertIn("proven", qualification["packaged_inference"])
        self.assertIn("st14-chat-2026-09-24.md", qualification["packaged_inference"],
                      "the claim does not name the evidence a reader can check")

    def test_the_activity_route_is_honest_when_idle_and_reports_a_real_completion(self) -> None:
        from components.hub.backend import activity as hub_activity

        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            client = self._client(engine)
            empty = client.get("/api/fleet/activity",
                               headers={"x-studio-token": TOKEN}).json()
            self.assertIsNone(empty["active"])
            self.assertIsNone(empty["latest"])
            self.assertIsNotNone(
                hub_activity.validate_snapshot(empty, expected_studio="chat"))

            # A real engine cannot load weights here; the route's own contract is what is under
            # test, so only the generation itself is stubbed.
            with mock.patch.object(engine, "complete", return_value={
                    "text": "hi", "elapsed_seconds": 0.01, "max_tokens": 4,
                    "prompt_tokens": 1, "json_valid": None, "repairs": 0}):
                response = client.post(
                    "/v1/chat/completions", headers={"x-studio-token": TOKEN},
                    json={"messages": [{"role": "user", "content": "hi"}],
                          "job_id": "batch-1:pack-0"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["id"], "batch-1:pack-0")
            latest = client.get("/api/fleet/activity",
                                headers={"x-studio-token": TOKEN}).json()

        self.assertIsNone(latest["active"])
        self.assertEqual(latest["latest"]["id"], "batch-1:pack-0")
        self.assertEqual(latest["latest"]["state"], "done")
        self.assertEqual(latest["latest"]["model"], self.MODEL_ID)
        validated = hub_activity.validate_snapshot(latest, expected_studio="chat")
        self.assertIsNotNone(validated, "the Hub refused the Chat worker's own snapshot")
        self.assertEqual(validated["latest"]["operation"], "chat")

    def test_a_second_model_is_refused_by_name_and_the_directory_to_add_to(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            response = self._client(engine).post(
                "/v1/chat/completions", headers={"x-studio-token": TOKEN},
                json={"model": "org/another-model",
                      "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(response.status_code, 404)
        detail = response.json()["detail"]
        self.assertIn(self.MODEL_ID, detail)
        self.assertIn("model-manifests", detail)
        self.assertIn("STUDIOHUB_CHAT_MANIFEST", detail)

    def test_the_health_route_reports_the_release_not_a_word(self) -> None:
        import re

        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            health = self._client(engine).get(
                "/api/health", headers={"x-studio-token": TOKEN}).json()
        self.assertNotEqual(health["app_version"], "standalone")
        self.assertTrue(
            re.fullmatch(r"\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?|unknown",
                         health["app_version"]),
            f"{health['app_version']!r} cannot be mistaken for a version",
        )

    def test_refused_parameters_are_named_rather_than_silently_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            client = self._client(engine)
            refused = client.post(
                "/v1/chat/completions", headers={"x-studio-token": TOKEN},
                json={"messages": [{"role": "user", "content": "hi"}],
                      "response_format": {"type": "xml"}})
            self.assertEqual(refused.status_code, 422)
            self.assertIn("json_object", refused.json()["detail"])
            negative = client.post(
                "/v1/chat/completions", headers={"x-studio-token": TOKEN},
                json={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 0})
            self.assertEqual(negative.status_code, 422)


if __name__ == "__main__":
    unittest.main()
