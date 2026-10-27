"""Local Chat Studio worker — engine, memory policy and API contract.

Runs entirely without model weights: the engine's filesystem/telemetry paths and the
FastAPI contract are exercised with a stub engine, so this suite is valid on any host and
does not pretend to be the missing packaged-inference proof.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from components import model_defaults
from components.chat.app.backend import memory_policy as memory_policy_module
from components.chat.app.backend.llm_engine import (DEFAULT_TEMPERATURE, DEFAULT_TOP_P,
                                                    FALLBACK_MAX_OUTPUT_TOKENS, ChatBusy,
                                                    ChatContextExceeded, ChatEngine,
                                                    ChatModelUnavailable,
                                                    engine_from_environment,
                                                    resolve_model_dir)
from components.chat.app.backend.main import create_app


TOKEN = "test-owner-token"
REVISION = "8b2b98c00a6b4d291155e4890773ca8f769aee53"


_MISSING = object()
_ORIGINAL_MLX_MODULES = {}


def setUpModule() -> None:
    """Keep this worker suite fake-only even when run with the bundled MLX Python."""
    mlx_lm = types.ModuleType("mlx_lm")
    mlx_lm.__path__ = []

    def not_stubbed(*_args, **_kwargs):  # pragma: no cover - names a broken fixture
        raise AssertionError("test reached an unstubbed fake mlx_lm function")

    mlx_lm.load = not_stubbed
    mlx_lm.stream_generate = not_stubbed
    sample_utils = types.ModuleType("mlx_lm.sample_utils")
    sample_utils.make_sampler = lambda **kwargs: ("sampler", kwargs)
    mlx_lm.sample_utils = sample_utils
    mlx_vlm = types.ModuleType("mlx_vlm")
    mlx_vlm.__path__ = []
    mlx_vlm.load = not_stubbed
    mlx_vlm.stream_generate = not_stubbed
    mlx = types.ModuleType("mlx")
    mlx.__path__ = []
    core = types.ModuleType("mlx.core")
    core.random = types.SimpleNamespace(seed=lambda _seed: None)
    core.clear_cache = lambda: None
    core.set_cache_limit = lambda _limit: None
    mlx.core = core
    replacements = {
        "mlx_lm": mlx_lm,
        "mlx_lm.sample_utils": sample_utils,
        "mlx_vlm": mlx_vlm,
        "mlx": mlx,
        "mlx.core": core,
    }
    for name, module in replacements.items():
        _ORIGINAL_MLX_MODULES[name] = sys.modules.get(name, _MISSING)
        sys.modules[name] = module


def tearDownModule() -> None:
    for name, module in _ORIGINAL_MLX_MODULES.items():
        if module is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _stream_text(generate):
    """Adapt an old text-returning fake to mlx-lm's measured response stream."""
    def stream(*args, **kwargs):
        kwargs.pop("prompt_progress_callback", None)
        yield types.SimpleNamespace(text=generate(*args, **kwargs))
    return stream


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
            self.assertEqual(engine.max_output_tokens, FALLBACK_MAX_OUTPUT_TOKENS)

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
    # No generation_config, so the worker fallback is this model's ceiling. The attribute and
    # model_limits() must agree, or the fixture contradicts itself.
    max_output_tokens = FALLBACK_MAX_OUTPUT_TOKENS
    supports_vision = False

    def __init__(self, *, verified: bool = False, supports_vision: bool = False) -> None:
        self._verified = verified
        self.supports_vision = supports_vision
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
    def test_default_mode_is_auto_and_shape_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = memory_policy_module.MemoryPolicy(Path(tmp), _FakeEngine())
            status = policy.status()
            # A machine that has never chosen selects `auto`, and the payload publishes what
            # that resolves to here rather than making a reader re-derive it.
            self.assertEqual(status["mode"], "auto")
            self.assertEqual(status["default_mode"], "auto")
            self.assertEqual(status["resolved_mode"], policy.machine_mode())
            self.assertIn(status["resolved_mode"], {"balanced", "immediate"})
            self.assertIsNotNone(status["idle_seconds"])
            self.assertEqual([option["mode"] for option in status["options"]],
                             ["auto", "performance", "balanced", "memory_saver", "immediate"])
            for option in status["options"]:
                self.assertTrue(str(option["detail"]).strip(), option)
                self.assertIn("label", option)
                self.assertIn("idle_seconds", option)
            self.assertIn("resident", status)
            self.assertIn("machine_bytes", status)

    def test_auto_resolves_per_machine_through_the_class(self) -> None:
        """The class is the chat contract; its answer must be the shared rule's."""
        with tempfile.TemporaryDirectory() as tmp:
            policy = memory_policy_module.MemoryPolicy(Path(tmp), _FakeEngine())
            original = memory_policy_module.machine_bytes
            try:
                memory_policy_module.machine_bytes = lambda: 8 * 1024 ** 3
                self.assertEqual(policy.machine_mode(), "immediate")
                self.assertEqual(policy.resolved_mode(), "immediate")
                self.assertEqual(policy.idle_seconds(), 0)
                memory_policy_module.machine_bytes = lambda: 32 * 1024 ** 3
                self.assertEqual(policy.machine_mode(), "balanced")
                self.assertEqual(policy.resolved_mode(), "balanced")
                self.assertEqual(policy.idle_seconds(), 600)
                memory_policy_module.machine_bytes = lambda: 0
                self.assertEqual(policy.machine_mode(), "immediate")
            finally:
                memory_policy_module.machine_bytes = original

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
            # `auto` would hold this model on a 16 GB or larger Mac; the mode under test is
            # immediate, so it is selected explicitly.
            policy.save("immediate")
            policy.tick()
            self.assertFalse(engine.loaded())
            self.assertEqual(policy.status()["last_release_reason"], "idle")
            self.assertEqual(policy.status()["release_count"], 1)

    def test_auto_releases_on_the_window_this_machine_resolves_to(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _FakeEngine()
            engine._loaded = True
            engine._last_activity = time.time() - 700
            policy = memory_policy_module.MemoryPolicy(Path(tmp), engine)
            self.assertEqual(policy.mode(), "auto")
            original = memory_policy_module.machine_bytes
            try:
                # A 32 GB Mac holds the model for the balanced window: 700 s idle releases.
                memory_policy_module.machine_bytes = lambda: 32 * 1024 ** 3
                policy.tick()
                self.assertFalse(engine.loaded())
                self.assertEqual(policy.status()["release_count"], 1)

                # An 8 GB Mac releases as soon as the job is done; 10 s idle is enough.
                engine._loaded = True
                engine._last_activity = time.time() - 10
                memory_policy_module.machine_bytes = lambda: 8 * 1024 ** 3
                policy.tick()
                self.assertFalse(engine.loaded())
            finally:
                memory_policy_module.machine_bytes = original

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
            policy.save("immediate")
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

    def test_catalog_qualification_uses_selected_model_evidence(self) -> None:
        engine = _FakeEngine(verified=True)
        engine.manifest = {"qualification": {"reason": "Owner accepted Gemma on 32 GiB, 2026-10-07; standalone packaged-runtime evidence."}}
        catalog = self._client(engine).get(
            "/api/catalog", headers={"x-studio-token": TOKEN}).json()["models"][0]
        self.assertEqual(catalog["qualification"]["packaged_inference"],
                         engine.manifest["qualification"]["reason"])
        self.assertNotIn("2026-09-24", catalog["qualification"]["packaged_inference"])

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
    # The base recorder states NO ceiling of its own — adding one is exactly what the
    # `_StatedLimitEngine` subclass exists to do — so its ceiling is the worker's fallback, and
    # `model_limits()` below says so. Attribute and published limit must agree, or the fixture
    # contradicts itself and the contract test fails.
    max_output_tokens = FALLBACK_MAX_OUTPUT_TOKENS
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
                "value": FALLBACK_MAX_OUTPUT_TOKENS,
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
        self.assertIn(str(engine.max_output_tokens), detail,
                      "the refusal does not name the published maximum")
        self.assertEqual(
            self._post(client, {**self.MESSAGE,
                                "max_tokens": engine.max_output_tokens + 1}).status_code, 422)
        engine.seen = []
        self.assertEqual(
            self._post(client, {**self.MESSAGE,
                                "max_tokens": engine.max_output_tokens}).status_code, 200)
        self.assertEqual(engine.seen[-1]["max_tokens"], engine.max_output_tokens)
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
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _CountedTokenizer(90))):
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
                    mock.patch("mlx_lm.stream_generate",
                               _stream_text(lambda *a, **k: "served")):
                outcome = engine.complete(self.MESSAGE, max_tokens=20)
        self.assertEqual(outcome["text"], "served")

    def test_nothing_is_refused_when_nothing_states_a_context_window(self) -> None:
        # 4 million tokens with no stated window: the worker must not invent a limit.
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp), context=None, tokens=4_000_000)
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _CountedTokenizer(4_000_000))), \
                    mock.patch("mlx_lm.stream_generate",
                               _stream_text(lambda *a, **k: "served")):
                outcome = engine.complete(self.MESSAGE, max_tokens=20)
        self.assertEqual(outcome["text"], "served")
        self.assertIsNone(engine.context_window_tokens)

    def test_the_route_refuses_a_prompt_past_the_context_before_streaming(self) -> None:
        # The prompt itself is past the model's stated context — what the name says. The prompt
        # used to be 90 of 100, so what actually refused the request was the lane's own default
        # budget not fitting, and that is no longer a refusal: a figure the request did not name
        # is shrunk to the room the context leaves (`_refuse_over_context`, `asked=False`), or a
        # model with a smaller context than the lane default could never serve an unsized
        # request at all. See `RealEngineIntegrationTests` for that half.
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp), context=100, tokens=150)
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _CountedTokenizer(150))):
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
                    self.assertIn("150", detail, "the refusal does not name the measured prompt")
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

        with mock.patch("mlx_lm.stream_generate", _stream_text(fake_generate)):
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
        # Sized so the request below — the worker's default output budget plus a prompt — fits
        # the context the model states. The old value was 8192, hand-tuned against a 4096-token
        # fallback; when the fallback went to 16,384 this fixture became the binding constraint
        # and the request was refused, which is the refusal working correctly. Keep it
        # comfortably above FALLBACK_MAX_OUTPUT_TOKENS so the fallback is what binds — 262,144
        # is Qwen3.5-9B 4-bit's own `max_position_embeddings`, and the fallback is 128,000 since
        # the owner's 2026-10-19 decision.
        (model_dir / "config.json").write_text(
            json.dumps({"text_config": {"max_position_embeddings": 262144}}))
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
                    mock.patch("mlx_lm.stream_generate", _stream_text(fake_generate)):
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
            self.assertEqual(engine.context_window_tokens, 262144)

    def test_a_context_smaller_than_the_fallback_still_serves_an_unsized_request(self) -> None:
        """A number this worker filled in may not refuse the request it was invented to serve.

        `_budget` uses `FALLBACK_MAX_OUTPUT_TOKENS` (128,000 since the owner's 2026-10-19
        decision) when the caller names nothing, and a model can state a much smaller context —
        gemma4-26b-a4b-qat-4bit states `qualification.context_window_tokens = 32768`. Refusing
        every unsized request on such a model would make it unusable for exactly the caller that
        asked for nothing, so the worker's own default shrinks to the room the context leaves,
        and the generation runs with that number.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text(
                json.dumps({"text_config": {"max_position_embeddings": 64}}))
            (model_dir / "model.safetensors").write_bytes(b"weights")
            (model_dir / "tokenizer.json").write_text("{}")
            engine = ChatEngine(model_dir=model_dir, model_id="mlx-community/Qwen3.5-9B-4bit",
                                revision="c" * 40)
            captured: dict = {}

            def fake_generate(model, tokenizer, prompt, **kwargs):
                captured["kwargs"] = kwargs
                return "ok"

            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _StubTokenizer())), \
                    mock.patch("mlx_lm.stream_generate", _stream_text(fake_generate)):
                policy = memory_policy_module.MemoryPolicy(root, engine)
                app = create_app(engine, policy, TOKEN, "b" * 64)
                with TestClient(app) as client:
                    response = client.post(
                        "/v1/chat/completions", headers={"x-studio-token": TOKEN},
                        json={"messages": [{"role": "user", "content": "hi"}]})

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(captured["kwargs"]["max_tokens"], 63,
                             "the room the 64-token context leaves after the 1-token prompt")

    def test_a_callers_own_number_over_the_context_is_still_refused(self) -> None:
        """The other half: a figure the caller chose is theirs to be refused, with the counts."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text(
                json.dumps({"text_config": {"max_position_embeddings": 64}}))
            (model_dir / "model.safetensors").write_bytes(b"weights")
            (model_dir / "tokenizer.json").write_text("{}")
            engine = ChatEngine(model_dir=model_dir, model_id="mlx-community/Qwen3.5-9B-4bit",
                                revision="c" * 40)

            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _StubTokenizer())):
                policy = memory_policy_module.MemoryPolicy(root, engine)
                app = create_app(engine, policy, TOKEN, "b" * 64)
                with TestClient(app) as client:
                    response = client.post(
                        "/v1/chat/completions", headers={"x-studio-token": TOKEN},
                        json={"messages": [{"role": "user", "content": "hi"}],
                              "max_tokens": 1000})

            self.assertEqual(response.status_code, 422, response.text)
            detail = response.json()["detail"]
            self.assertIn("1000", detail)
            self.assertIn("64", detail)

    def test_engine_from_environment_resolves_the_library_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp) / "models"
            (models / "qwen3-5-9b-4bit" / ("c" * 40) / "model").mkdir(parents=True)
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps({
                "package_id": "qwen3-5-9b-4bit", "revision": "c" * 40,
                "model_id": "mlx-community/Qwen3.5-9B-4bit",
                "qualification": {"context_window_tokens": 65536},
            }))
            with mock.patch.dict(os.environ, {
                    "STUDIOHUB_MODELS_DIR": str(models),
                    "STUDIOHUB_CHAT_MANIFEST": str(manifest)}):
                engine = engine_from_environment()
        self.assertEqual(engine.model_dir, models / "qwen3-5-9b-4bit" / ("c" * 40) / "model")
        self.assertEqual(engine.context_window_tokens, 65536)
        self.assertEqual(engine.max_output_tokens, FALLBACK_MAX_OUTPUT_TOKENS)


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
                    mock.patch("mlx_lm.stream_generate",
                               _stream_text(lambda *a, **k: "served")):
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
                    mock.patch("mlx_lm.stream_generate",
                               _stream_text(lambda *a, **k: "served")):
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
                    mock.patch("mlx_lm.stream_generate",
                               _stream_text(lambda *a, **k: "served")):
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
                    mock.patch("mlx_lm.stream_generate",
                               _stream_text(lambda *a, **k: "served")):
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


class GenerationTimeTests(unittest.TestCase):
    """The worker states how long it *generated*, never how long the request took.

    Both paths must mean the same thing by it: `complete()`'s `elapsed_seconds` and the value
    `stream()` returns both start after the model is resident, so a cold turn's model load is in
    neither. The rate the app shows is the stated `usage.completion_tokens` over this number; a
    number that included the load would understate the model on exactly the first reply and then
    change meaning on the second.

    Each test pins that by measuring the load: a monkeypatched slow `_load_locked` (a slow
    `mlx_lm.load`) has to happen - the wall clock proves it did - while the number the worker
    states stays far below it.
    """

    # Long enough for the load to be unmistakable, with the stated window an order of magnitude
    # below it, so a scheduling stall cannot make a route-measured number look engine-measured.
    LOAD_SECONDS = 0.5
    GENERATE_SECONDS = 0.04

    def _engine(self, root: Path) -> ChatEngine:
        model_dir = root / "model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text(
            json.dumps({"text_config": {"max_position_embeddings": 262144}}))
        (model_dir / "model.safetensors").write_bytes(b"weights")
        (model_dir / "tokenizer.json").write_text("{}")
        return ChatEngine(model_dir=model_dir, model_id="mlx-community/Qwen3.5-9B-4bit",
                          revision="r" * 40)

    def _slow_load(self):
        def load(*args, **kwargs):
            time.sleep(self.LOAD_SECONDS)
            return object(), _TemplateTokenizer()
        return load

    def _slow_stream(self, pieces=("Phnom", " Penh")):
        def stream_generate(*args, **kwargs):
            for piece in pieces:
                time.sleep(self.GENERATE_SECONDS)
                yield type("R", (), {"text": piece})()
        return stream_generate

    @staticmethod
    def _consume(stream) -> tuple[list[str], float | None]:
        """Consume a stream, returning its chunks and the window it *returned*.

        The return value is the streaming path's measurement; a consumer that only iterates (and
        so never reads it) simply states none.
        """
        chunks: list[str] = []
        while True:
            try:
                chunks.append(next(stream))
            except StopIteration as stop:
                return chunks, stop.value

    def test_a_completion_states_generation_time_and_not_the_model_load(self) -> None:
        def slow_generate(*args, **kwargs):
            time.sleep(self.GENERATE_SECONDS)
            return "served"

        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            started = time.time()
            with mock.patch("mlx_lm.load", self._slow_load()), \
                    mock.patch("mlx_lm.stream_generate", _stream_text(slow_generate)):
                outcome = engine.complete([{"role": "user", "content": "hi"}], max_tokens=8)
            wall = time.time() - started
        self.assertGreaterEqual(wall, self.LOAD_SECONDS, "the slow model load did not happen")
        self.assertGreaterEqual(outcome["elapsed_seconds"], self.GENERATE_SECONDS * 0.5,
                                "the generation itself is not in the stated time")
        self.assertLess(
            outcome["elapsed_seconds"], self.LOAD_SECONDS / 2,
            "the model load is inside the stated generation time: a rate derived from it would "
            "understate the model on the turn the owner is measuring")

    def test_a_stream_returns_the_generation_window_it_measured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            started = time.time()
            with mock.patch("mlx_lm.load", self._slow_load()), \
                    mock.patch("mlx_lm.stream_generate", self._slow_stream()):
                chunks, stated = self._consume(
                    engine.stream([{"role": "user", "content": "hi"}], max_tokens=8))
            wall = time.time() - started
        self.assertEqual(chunks, ["Phnom", " Penh"])
        self.assertGreaterEqual(wall, self.LOAD_SECONDS, "the slow model load did not happen")
        self.assertIsInstance(stated, float, "the stream stated no generation window")
        self.assertGreaterEqual(stated, self.GENERATE_SECONDS * 0.5)
        self.assertLess(stated, self.LOAD_SECONDS / 2,
                        "the model load is inside the stream's stated generation window")

    def test_the_streamed_final_chunk_states_the_engines_own_measured_elapsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            policy = memory_policy_module.MemoryPolicy(Path(tmp), engine)
            app = create_app(engine, policy, TOKEN, "a" * 64)
            started = time.time()
            # The prompt pre-flight is bypassed so the slow load happens *inside* `stream()`, on
            # the first step the route takes - the one case a window measured by the route would
            # pick the load up in. The engine's own window starts after the model is resident and
            # must not contain it.
            with mock.patch.object(ChatEngine, "check_prompt_fits", lambda *a, **k: None), \
                    mock.patch("mlx_lm.load", self._slow_load()), \
                    mock.patch("mlx_lm.stream_generate", self._slow_stream()):
                with TestClient(app) as client:
                    response = client.post(
                        "/v1/chat/completions", headers={"x-studio-token": TOKEN},
                        json={"messages": [{"role": "user", "content": "hi"}], "stream": True})
            wall = time.time() - started
        self.assertEqual(response.status_code, 200, response.text)
        events = [line[len("data:"):].strip() for line in response.text.splitlines()
                  if line.startswith("data:")]
        self.assertEqual(events[-1], "[DONE]")
        final = json.loads(events[-2])
        self.assertEqual(final["choices"][0]["finish_reason"], "stop")
        self.assertEqual(final["usage"]["completion_tokens"], 2)
        stated = final["studio_hub"]["elapsed_seconds"]
        self.assertGreaterEqual(wall, self.LOAD_SECONDS, "the slow model load did not happen")
        self.assertGreaterEqual(stated, self.GENERATE_SECONDS * 0.5)
        self.assertLess(stated, self.LOAD_SECONDS / 2,
                        "the route published a number measured around the stream rather than "
                        "the engine's own generation window")

    def test_a_stream_that_states_no_window_publishes_no_number(self) -> None:
        # `_FakeEngine.stream` returns nothing at all. A worker whose engine measures nothing
        # must publish nothing: the route does not fill the gap with a round trip of its own.
        with tempfile.TemporaryDirectory() as tmp:
            policy = memory_policy_module.MemoryPolicy(Path(tmp), _FakeEngine(verified=True))
            app = create_app(_FakeEngine(verified=True), policy, TOKEN, "a" * 64)
            with TestClient(app) as client:
                response = client.post(
                    "/v1/chat/completions", headers={"x-studio-token": TOKEN},
                    json={"messages": [{"role": "user", "content": "hi"}], "stream": True})
        self.assertEqual(response.status_code, 200, response.text)
        events = [line[len("data:"):].strip() for line in response.text.splitlines()
                  if line.startswith("data:")]
        final = json.loads(events[-2])
        self.assertEqual(final["choices"][0]["finish_reason"], "stop")
        self.assertNotIn("studio_hub", final,
                         "a number the engine never stated was published as a measurement")


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
                               self._stream_generate(chunks=40, delay=0.1)):
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
                        with mock.patch("mlx_lm.stream_generate",
                                        _stream_text(lambda *a, **k: "served")):
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
                    mock.patch("mlx_lm.stream_generate", _stream_text(fake_generate)):
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
                    mock.patch("mlx_lm.stream_generate",
                               _stream_text(lambda *a, **k: "ok")), \
                    mock.patch.object(sys.modules["mlx.core"].random, "seed") as seed_call:
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
                    mock.patch("mlx_lm.stream_generate", _stream_text(fake_generate)):
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
            # The machine-readable half of the same decision (HUB-20): the checkpoint's own file
            # states it, so this is the model's figure.
            "source_kind": "model",
        })
        self.assertEqual(catalog["model_limits"]["max_output_tokens"], {
            "value": FALLBACK_MAX_OUTPUT_TOKENS,
            "source": "worker default: the checkpoint ships no generation_config.json",
            # **This is the number that used to arrive at Story Studio labelled as the model's
            # own.** The gateway relayed it as `provider` for want of a token; now the sheet
            # says what it is.
            "source_kind": "worker_default",
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
            "source_kind": "model",
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

    def test_catalog_without_model_specific_proof_does_not_invent_it(self) -> None:
        # A legacy fixture with no evidence must not inherit another model's proof.
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            qualification = self._client(engine).get(
                "/api/catalog", headers={"x-studio-token": TOKEN}).json()[
                    "models"][0]["qualification"]
        self.assertNotIn("pending", qualification["packaged_inference"])
        self.assertIn("no model-specific inference evidence", qualification["packaged_inference"])
        self.assertNotIn("st14-chat-2026-09-24.md", qualification["packaged_inference"])

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
        self.assertIn("STUDIOHUB_MODEL_MANIFESTS", detail)

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


class _StatedLimitEngine(_RecordingEngine):
    """A model that states an output ceiling of its own, above this worker's fallback.

    The shipped Qwen3.5-9B 4-bit ships no `generation_config.json`, so this worker's fallback is
    the only bound, and a stated ceiling has to sit *above* it for the two to be told apart.
    That is what this fixture is for, so its value must move whenever the fallback moves — it
    was 8192 against a 4096 fallback, and at 16,384 that silently inverted its purpose. 262,144
    is Qwen3.5-9B 4-bit's own `max_position_embeddings`, and it stays above the 128,000 fallback.
    """

    max_output_tokens = 262144

    def model_limits(self) -> dict:
        return {
            "context_window_tokens": {
                "value": 262144,
                "source": "model/config.json text_config.max_position_embeddings"},
            "max_output_tokens": {
                "value": _StatedLimitEngine.max_output_tokens,
                "source": "model/generation_config.json max_new_tokens"},
        }


class ModelDefaultsRouteTests(unittest.TestCase):
    """Per-model defaults: stored per machine, offered from the lane's own surface, never an override.

    The owner's rule is *"it has to be per model because each model is weight differently"*, so
    every assertion here is about one model's own numbers: the surface offered is the lane
    contract's parameter list (minus what is required per request), the bounds are the bounds the
    route enforces, and the value published as a `default` is the value the route serves with.
    """

    MESSAGE = {"messages": [{"role": "user", "content": "hi"}]}

    def _fixture(self, engine=None):
        engine = engine or _StatedLimitEngine()
        self.engine = engine
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        data = Path(temporary.name)
        policy = memory_policy_module.MemoryPolicy(data, engine)
        return TestClient(create_app(engine, policy, TOKEN, "a" * 64)), engine, data

    def _headers(self) -> dict:
        return {"x-studio-token": TOKEN}

    def _stored(self, client) -> dict:
        response = client.get("/api/model-defaults", headers=self._headers())
        self.assertEqual(response.status_code, 200, response.text)
        models = response.json()["models"]
        self.assertEqual(len(models), 1, "this worker serves one model")
        return models[0]

    def _put(self, client, values: dict, *, repo: str | None = None):
        return client.put("/api/model-defaults", headers=self._headers(),
                          json={"repo": repo if repo is not None else self.engine.model_id,
                                "values": values})

    def _post(self, client, body: dict):
        return client.post("/v1/chat/completions", headers=self._headers(),
                           json={**self.MESSAGE, **body})

    def _catalog_contract(self, client) -> dict:
        return client.get("/api/catalog", headers=self._headers()).json()[
            "models"][0]["lane_contract"]

    def test_the_route_round_trips_and_returns_to_the_lanes_own(self) -> None:
        client, engine, data = self._fixture()
        lane = _StatedLimitEngine.model_id

        before = self._stored(client)
        self.assertEqual(before["repo"], lane)
        # GET before anything is set is the lane's own answer, and reading wrote nothing.
        self.assertEqual(before["chosen"], {})
        self.assertEqual(before["defaults"], before["stated"],
                         "a fresh worker does not answer with the lane's own defaults")
        self.assertFalse(model_defaults.settings_file(data).exists(),
                         "reading the defaults wrote a settings file")

        # The distinction the owner asked for, with a negative control: this model states a
        # ceiling of its own, and the default this lane serves with is still the worker's own
        # fallback - not the model's ceiling.
        self.assertEqual(engine.max_output_tokens, _StatedLimitEngine.max_output_tokens)
        self.assertEqual(before["defaults"]["max_tokens"], FALLBACK_MAX_OUTPUT_TOKENS)
        self.assertNotEqual(before["defaults"]["max_tokens"], engine.max_output_tokens)
        published = {parameter["name"]: parameter for parameter in before["parameters"]}
        self.assertEqual(published["max_tokens"]["maximum"], engine.max_output_tokens)

        # The offered surface is derived from the lane contract, never restated: it is exactly what
        # `model_defaults.editable` derives from the lane's own list, and every lane parameter that
        # is *not* offered is excluded for one of the two stated reasons. Set equality against the
        # non-required list alone would be wrong in both directions — `model` is not required but
        # has one possible value, so it is not a control, and the moment this worker serves a second
        # model that enum grows and the control appears with no code change here.
        lane_parameters = self._catalog_contract(client)["parameters"]
        self.assertEqual(
            set(published),
            {parameter["name"] for parameter in model_defaults.editable(lane_parameters)},
            "the defaults surface restates the lane's parameters instead of using it")
        self.assertTrue(set(published) <= {parameter["name"] for parameter in lane_parameters},
                        "a control was offered for a parameter this lane does not publish")
        for parameter in lane_parameters:
            if parameter["name"] in published:
                continue
            options = parameter.get("enum")
            reason = ("a request must supply it" if parameter.get("required")
                      else "it has exactly one possible value" if isinstance(options, list)
                      and len(options) == 1 else "neither reason applies")
            self.assertNotEqual(reason, "neither reason applies",
                                f"{parameter['name']} is not offered and not for a stated reason")

        saved = self._put(client, {"max_tokens": 2048})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["models"][0]["defaults"]["max_tokens"], 2048)
        self.assertEqual(saved.json()["models"][0]["chosen"], {"max_tokens": 2048})
        self.assertTrue(model_defaults.settings_file(data).exists(),
                        "the default was not stored beside memory_policy.json")
        self.assertEqual(self._stored(client)["defaults"]["max_tokens"], 2048)

        cleared = client.request("DELETE", "/api/model-defaults", headers=self._headers(),
                                 json={"repo": lane})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json()["models"][0]["defaults"],
                         cleared.json()["models"][0]["stated"],
                         "delete did not return the model to what the lane itself states")
        self.assertEqual(cleared.json()["models"][0]["chosen"], {})

        # The repo may arrive as a query parameter too, and that spelling clears as well.
        self.assertEqual(self._put(client, {"max_tokens": 1024}).status_code, 200)
        by_query = client.request("DELETE", "/api/model-defaults", headers=self._headers(),
                                  params={"repo": lane})
        self.assertEqual(by_query.status_code, 200, by_query.text)
        self.assertEqual(by_query.json()["models"][0]["defaults"]["max_tokens"],
                         FALLBACK_MAX_OUTPUT_TOKENS)

    def test_a_value_outside_the_bound_is_refused_by_name_and_not_stored(self) -> None:
        client, engine, data = self._fixture()
        # One entry per bound this lane enforces: its own maximum and minimums (including
        # top_p's exclusive one), a wrong type, a JSON mode this engine cannot serve, a parameter
        # this lane does not publish, and a model this worker does not serve.
        for values, name, expected in (
            ({"max_tokens": engine.max_output_tokens + 1}, "max_tokens",
             "above the lane's maximum"),
            ({"max_tokens": 0}, "max_tokens", "below the lane's minimum"),
            ({"max_tokens": "4096"}, "max_tokens", "expected integer, got str"),
            ({"temperature": -0.1}, "temperature", "below the lane's minimum"),
            ({"top_p": 0}, "top_p", "greater than 0"),
            ({"top_p": 1.5}, "top_p", "above the lane's maximum"),
            ({"response_format": {"type": "xml"}}, "response_format", "json_object"),
            ({"seed": 4.5}, "seed", "expected integer, got float"),
            ({"cfg_scale": 3.5}, "cfg_scale", "publishes no such settable parameter"),
            # `model` has one possible value, so it is not a settable parameter at all and is
            # refused for that reason rather than for the value. The route still 404s a request
            # naming another model (pinned above); this is the store refusing to hold one.
            ({"model": "org/another-model"}, "model", "publishes no such settable parameter"),
        ):
            with self.subTest(values=values):
                response = self._put(client, values)
                self.assertEqual(response.status_code, 422,
                                 f"{values} was accepted: {response.text}")
                detail = response.json()["detail"]
                self.assertIsInstance(detail, dict, "the refusal is not per-parameter")
                self.assertIn(name, detail, f"{values} was refused without naming the parameter")
                self.assertIn(expected, detail[name])
                self.assertFalse(model_defaults.settings_file(data).exists(),
                                 f"{values} was stored despite being refused")
        self.assertEqual(self._stored(client)["defaults"]["max_tokens"],
                         FALLBACK_MAX_OUTPUT_TOKENS)

    def test_every_problem_is_reported_at_once_and_nothing_is_half_stored(self) -> None:
        client, engine, data = self._fixture()
        self.assertEqual(self._put(client, {"max_tokens": 2048}).status_code, 200)
        response = self._put(client, {"max_tokens": engine.max_output_tokens + 1,
                                      "temperature": -1, "cfg_scale": 1})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(sorted(response.json()["detail"]),
                         ["cfg_scale", "max_tokens", "temperature"],
                         "an operator who typed three bad values should not have to save three "
                         "times to find out")
        # The previous save survives intact: a refused save half-applies nothing.
        self.assertEqual(self._stored(client)["defaults"]["max_tokens"], 2048)

    def test_a_non_finite_number_is_refused_rather_than_stored(self) -> None:
        # Python's JSON parser accepts the bare tokens NaN/Infinity, so they have to be caught
        # after parsing: a stored `Infinity` would turn every later request that omitted
        # temperature into a 422 the operator could only clear by hand-editing the file.
        client, engine, data = self._fixture()
        raw = '{"repo": %s, "values": {"temperature": Infinity}}' % json.dumps(engine.model_id)
        response = client.request(
            "PUT", "/api/model-defaults",
            headers={**self._headers(), "content-type": "application/json"}, content=raw)
        self.assertEqual(response.status_code, 422, response.text)
        detail = response.json()["detail"]
        self.assertIn("temperature", detail)
        self.assertIn("finite", detail["temperature"])
        self.assertFalse(model_defaults.settings_file(data).exists())

    def test_an_explicit_request_still_wins_and_an_omitted_one_takes_the_default(self) -> None:
        client, engine, _data = self._fixture()
        self.assertEqual(self._put(client, {"max_tokens": 2048, "temperature": 0.1}).status_code,
                         200)

        # Negative control: with 2048 stored, an omitted max_tokens reaches the engine as 2048
        # rather than the lane's 4096 fallback.
        engine.seen = []
        self.assertEqual(self._post(client, {}).status_code, 200)
        self.assertEqual(engine.seen[-1]["max_tokens"], 2048)
        self.assertEqual(engine.seen[-1]["temperature"], 0.1)

        # And what the request states always wins, over the stored default and the fallback.
        engine.seen = []
        self.assertEqual(self._post(client, {"max_tokens": 512, "temperature": 0.9}).status_code,
                         200)
        self.assertEqual(engine.seen[-1]["max_tokens"], 512)
        self.assertEqual(engine.seen[-1]["temperature"], 0.9)

        # The alias the lane honours is a supplied value too, not an omission.
        engine.seen = []
        self.assertEqual(self._post(client, {"max_completion_tokens": 256}).status_code, 200)
        self.assertEqual(engine.seen[-1]["max_tokens"], 256)

    def test_the_catalogs_published_default_is_the_value_the_route_uses(self) -> None:
        client, engine, _data = self._fixture()

        # Read the catalog before anything is stored: its `default` is the lane's own, and the
        # route uses exactly that when the request omits the parameter.
        published = {parameter["name"]: parameter for parameter in
                     self._catalog_contract(client)["parameters"]}
        self.assertEqual(published["max_tokens"]["default"], FALLBACK_MAX_OUTPUT_TOKENS)
        engine.seen = []
        self.assertEqual(self._post(client, {}).status_code, 200)
        self.assertEqual(engine.seen[-1]["max_tokens"], published["max_tokens"]["default"])

        self.assertEqual(self._put(client, {"max_tokens": 3000, "temperature": 0.25}).status_code,
                         200)
        contract = self._catalog_contract(client)
        published = {parameter["name"]: parameter for parameter in contract["parameters"]}
        self.assertEqual(published["max_tokens"]["default"], 3000)
        self.assertEqual(published["temperature"]["default"], 0.25)
        engine.seen = []
        self.assertEqual(self._post(client, {}).status_code, 200)
        self.assertEqual(engine.seen[-1]["max_tokens"], published["max_tokens"]["default"],
                         "the catalog advertises a default the route does not use")
        self.assertEqual(engine.seen[-1]["temperature"], published["temperature"]["default"])
        # The row's other published defaults are the same resolution, not a second copy.
        catalog = client.get("/api/catalog", headers=self._headers()).json()["models"][0]
        self.assertEqual(catalog["generation_defaults"],
                         {"temperature": published["temperature"]["default"],
                          "top_p": published["top_p"]["default"]})

    def test_an_unknown_repo_is_a_named_refusal_rather_than_a_silent_no_op(self) -> None:
        client, engine, data = self._fixture()
        for response in (
            self._put(client, {"max_tokens": 16}, repo="org/another-model"),
            client.request("DELETE", "/api/model-defaults", headers=self._headers(),
                           json={"repo": "org/another-model"}),
        ):
            self.assertEqual(response.status_code, 404, response.text)
            detail = response.json()["detail"]
            self.assertIn("org/another-model", detail,
                          "the refusal does not name the repo that was sent")
            self.assertIn(engine.model_id, detail,
                          "the refusal does not name the one model this worker serves")
        self.assertFalse(model_defaults.settings_file(data).exists(),
                         "a write for an unknown repo stored something anyway")
        # Naming no repo at all is refused too, rather than quietly meaning "the only model".
        missing = client.put("/api/model-defaults", headers=self._headers(), json={"values": {}})
        self.assertEqual(missing.status_code, 422)
        self.assertIn("repo", missing.json()["detail"])

    def test_the_existing_refusals_are_unmoved_by_a_stored_default(self) -> None:
        client, engine, _data = self._fixture()
        self.assertEqual(self._put(client, {"max_tokens": 1024, "thinking": True}).status_code,
                         200)

        # max_tokens is still bounded by the ceiling this model states, whatever is stored.
        above = self._post(client, {"max_tokens": engine.max_output_tokens + 1})
        self.assertEqual(above.status_code, 422)
        detail = above.json()["detail"]
        self.assertIn(str(engine.max_output_tokens), detail)
        self.assertIn("max_tokens", detail)

        # thinking is still a real JSON boolean: a stored `true` does not turn "false" into true
        # and does not excuse a string.
        for value in ("false", "no", 0, 1, None):
            with self.subTest(thinking=value):
                refused = self._post(client, {"thinking": value})
                self.assertEqual(refused.status_code, 422, f"thinking={value!r} was accepted")
                self.assertIn("thinking", refused.json()["detail"])
        self.assertEqual(engine.seen, [], "a refused request reached the engine")

    def test_a_prompt_past_the_models_context_is_still_refused_by_the_measured_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text(
                json.dumps({"text_config": {"max_position_embeddings": 100}}))
            (model_dir / "model.safetensors").write_bytes(b"weights")
            (model_dir / "tokenizer.json").write_text("{}")
            engine = ChatEngine(model_dir=model_dir, model_id="mlx-community/Qwen3.5-9B-4bit",
                                revision="r" * 40)
            self.engine = engine
            policy = memory_policy_module.MemoryPolicy(root, engine)
            client = TestClient(create_app(engine, policy, TOKEN, "a" * 64))
            # A stored output budget does not buy room past the context the model states.
            self.assertEqual(self._put(client, {"max_tokens": 500}).status_code, 200)
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), _CountedTokenizer(90))):
                for body in ({}, {"max_tokens": 20}):
                    with self.subTest(body=body):
                        response = self._post(client, body)
                        self.assertEqual(response.status_code, 422)
                        detail = response.json()["detail"]
                        self.assertIn("messages", detail)
                        self.assertIn("90", detail, "the refusal does not name the measured count")
                        self.assertIn("100", detail, "the refusal does not name the model's number")

    def test_a_second_model_cannot_be_served_defaults_under_this_workers_repo(self) -> None:
        # The repo a default is stored under is the one model this worker serves, so a second
        # chat model in the same fleet cannot inherit the first one's numbers.
        client, engine, data = self._fixture()
        self.assertEqual(self._put(client, {"max_tokens": 2048}).status_code, 200)
        stored = json.loads(model_defaults.settings_file(data).read_text())
        self.assertEqual(list(stored), [engine.model_id])
        self.assertEqual(stored[engine.model_id], {"max_tokens": 2048})

    def test_a_stored_value_the_lane_now_refuses_is_never_applied_or_published(self) -> None:
        """A model update that tightens a bound must not turn a stored number into a refused one.

        The file is written directly because the route refuses to store this value now: it stands
        for a default stored while the bound was looser, which is the only way it can exist.
        """
        client, engine, data = self._fixture()
        model_defaults.settings_file(data).write_text(json.dumps(
            {engine.model_id: {"max_tokens": engine.max_output_tokens + 1}}))
        status = self._stored(client)
        self.assertEqual(status["defaults"]["max_tokens"], FALLBACK_MAX_OUTPUT_TOKENS,
                         "a stored value the lane refuses was applied anyway")
        self.assertIn("max_tokens", status["not_used"],
                      "the stored value is silently ignored rather than named")
        published = {parameter["name"]: parameter for parameter
                     in self._catalog_contract(client)["parameters"]}
        self.assertEqual(published["max_tokens"]["default"], FALLBACK_MAX_OUTPUT_TOKENS,
                         "the catalog advertises a default the route would refuse")
        engine.seen = []
        self.assertEqual(self._post(client, {}).status_code, 200)
        self.assertEqual(engine.seen[-1]["max_tokens"], FALLBACK_MAX_OUTPUT_TOKENS)


class ChatVisionWorkerTests(unittest.TestCase):
    def test_a_multimodal_checkpoint_is_detected_from_its_own_config(self) -> None:
        """The checkpoint states it, so a curated manifest cannot go stale behind the weights."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            multimodal = root / "a" / "model"
            text_only = root / "b" / "model"
            for model_dir, config in ((multimodal, {"vision_config": {"model_type": "gemma4_vision"}}),
                                      (text_only, {"text_config": {"hidden_size": 2816}})):
                model_dir.mkdir(parents=True)
                (model_dir / "config.json").write_text(json.dumps(config))
            self.assertTrue(ChatEngine(model_dir=multimodal, model_id="v", revision="r").supports_vision)
            self.assertFalse(ChatEngine(model_dir=text_only, model_id="t", revision="r").supports_vision)

    def test_a_manifest_may_still_withhold_vision(self) -> None:
        """The manifest's digest is pinned by install receipts, so it can say no — never yes."""
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text(json.dumps({"vision_config": {"model_type": "gemma4_vision"}}))
            withheld = ChatEngine(model_dir=model_dir, model_id="v", revision="r",
                                  manifest={"vision": False})
        self.assertFalse(withheld.supports_vision)

    def test_catalog_publishes_supports_vision_and_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = memory_policy_module.MemoryPolicy(Path(tmp), _FakeEngine(verified=True, supports_vision=True))
            app = create_app(_FakeEngine(verified=True, supports_vision=True), policy, TOKEN, "a" * 64)
            with TestClient(app) as client:
                catalog = client.get("/api/catalog", headers={"x-studio-token": TOKEN}).json()["models"][0]
            self.assertTrue(catalog["supports_vision"])
            self.assertIn("vision", catalog["capabilities"])
            self.assertIn("chat.completion", catalog["capabilities"])
            self.assertIn("content parts", catalog["lane_contract"]["parameters"][1]["note"])

    def test_catalog_publishes_text_only_when_supports_vision_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = memory_policy_module.MemoryPolicy(Path(tmp), _FakeEngine(verified=True, supports_vision=False))
            app = create_app(_FakeEngine(verified=True, supports_vision=False), policy, TOKEN, "a" * 64)
            with TestClient(app) as client:
                catalog = client.get("/api/catalog", headers={"x-studio-token": TOKEN}).json()["models"][0]
            self.assertFalse(catalog["supports_vision"])
            self.assertNotIn("vision", catalog["capabilities"])
            self.assertEqual(catalog["capabilities"], ["chat.completion"])

    def test_completion_accepts_content_parts_on_vision_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = memory_policy_module.MemoryPolicy(Path(tmp), _FakeEngine(verified=True, supports_vision=True))
            app = create_app(_FakeEngine(verified=True, supports_vision=True), policy, TOKEN, "a" * 64)
            with TestClient(app) as client:
                response = client.post(
                    "/v1/chat/completions", headers={"x-studio-token": TOKEN},
                    json={"messages": [
                        {"role": "user", "content": [
                            {"type": "text", "text": "describe this image"},
                            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQSkZJRg=="}},
                        ]}
                    ]}
                )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["choices"][0]["message"]["content"], "Once upon a time.")

    def test_completion_refuses_content_parts_on_text_only_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = memory_policy_module.MemoryPolicy(Path(tmp), _FakeEngine(verified=True, supports_vision=False))
            app = create_app(_FakeEngine(verified=True, supports_vision=False), policy, TOKEN, "a" * 64)
            with TestClient(app) as client:
                response = client.post(
                    "/v1/chat/completions", headers={"x-studio-token": TOKEN},
                    json={"messages": [
                        {"role": "user", "content": [
                            {"type": "text", "text": "describe this image"},
                            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQSkZJRg=="}},
                        ]}
                    ]}
                )
            self.assertEqual(response.status_code, 422)
            self.assertIn("does not accept images", response.json()["detail"])

    def test_completion_refuses_unsupported_part_type_on_vision_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = memory_policy_module.MemoryPolicy(Path(tmp), _FakeEngine(verified=True, supports_vision=True))
            app = create_app(_FakeEngine(verified=True, supports_vision=True), policy, TOKEN, "a" * 64)
            with TestClient(app) as client:
                response = client.post(
                    "/v1/chat/completions", headers={"x-studio-token": TOKEN},
                    json={"messages": [
                        {"role": "user", "content": [
                            {"type": "text", "text": "hello"},
                            {"type": "audio", "audio": "data:..."},
                        ]}
                    ]}
                )
            self.assertEqual(response.status_code, 422)
            self.assertIn("messages need string content or text/image_url parts", response.json()["detail"])

    def test_a_runtime_without_mlx_vlm_still_serves_text_and_names_what_is_missing(self) -> None:
        """The fleet's only chat checkpoint is multimodal; losing text chat with it is not an option."""
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text(json.dumps({"vision_config": {"model_type": "gemma4_vision"}}))
            (model_dir / "model.safetensors").write_bytes(b"weights")
            (model_dir / "tokenizer.json").write_text("{}")
            engine = ChatEngine(model_dir=model_dir, model_id="mlx-community/gemma-4-26B-A4B-it-qat-4bit",
                                revision="r" * 40)
            self.assertTrue(engine.supports_vision)
            with mock.patch.dict(sys.modules, {"mlx_vlm": None}), \
                    mock.patch("mlx_lm.load", lambda *a, **k: (object(), _StubTokenizer())), \
                    mock.patch("mlx_lm.stream_generate", _stream_text(lambda *a, **k: "text answer")):
                outcome = engine.complete([{"role": "user", "content": "hi"}], max_tokens=8)
            self.assertEqual(outcome["text"], "text answer")
            self.assertFalse(engine.supports_vision)
            self.assertIn("no mlx-vlm", engine.vision_unavailable_reason)

    def test_text_only_request_on_a_vision_model_uses_the_vision_loader(self) -> None:
        """A model mlx_vlm loaded cannot be driven by mlx_lm — even when no image is sent."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text(json.dumps({"vision_config": {"model_type": "gemma4_vision"}}))
            (model_dir / "model.safetensors").write_bytes(b"weights")
            (model_dir / "tokenizer.json").write_text("{}")
            engine = ChatEngine(model_dir=model_dir, model_id="mlx-community/gemma-4-26B-A4B-it-qat-4bit",
                                revision="r" * 40, manifest={})

            class _FakeProcessor:
                tokenizer = None

                def __init__(self):
                    self.tokenizer = _StubTokenizer()

                def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
                    return "<|user|>" + messages[-1]["content"] + "<|assistant|>"

            seen = {}

            def fake_vlm_generate(model, processor, prompt=None, image=None, **kwargs):
                seen["prompt"], seen["image"] = prompt, image
                yield type("R", (), {"text": "plain answer"})()

            with mock.patch("mlx_vlm.load", lambda *a, **k: (object(), _FakeProcessor())), \
                    mock.patch("mlx_vlm.stream_generate", fake_vlm_generate), \
                    mock.patch("mlx_lm.stream_generate",
                               lambda *a, **k: (_ for _ in ()).throw(AssertionError(
                                   "a vision model reached mlx_lm's generator"))):
                outcome = engine.complete([{"role": "user", "content": "hi"}], max_tokens=8)

        self.assertEqual(outcome["text"], "plain answer")
        self.assertIsNone(seen["image"], "a text-only request must not carry an image")

    def test_real_engine_vision_load_and_stream_generate(self) -> None:
        import base64
        import io
        from PIL import Image

        img = Image.new("RGB", (1, 1), color="red")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        data_uri = f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text(json.dumps({
                "vision_config": {"model_type": "gemma4_vision"},
                "text_config": {"max_position_embeddings": 262144}}))
            (model_dir / "model.safetensors").write_bytes(b"weights")
            (model_dir / "tokenizer.json").write_text("{}")

            engine = ChatEngine(model_dir=model_dir, model_id="mlx-community/gemma-4-26B-A4B-it-qat-4bit",
                                revision="r" * 40, manifest={})
            self.assertTrue(engine.supports_vision)

            loaded_with = []
            generated_with = []

            class _FakeProcessor:
                def __init__(self):
                    self.tokenizer = _StubTokenizer()

                def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
                    text = " ".join(m["content"] if isinstance(m["content"], str) else
                                    "".join(p.get("text", "<|image|>") if p.get("type") == "text"
                                            else "<|image|>" for p in m["content"])
                                    for m in messages)
                    return f"<|user|>{text}<|assistant|>"

            def fake_vlm_load(path, **kwargs):
                loaded_with.append(path)
                return object(), _FakeProcessor()

            def fake_vlm_generate(model, processor, prompt=None, image=None, **kwargs):
                generated_with.append({"prompt": prompt, "image": image, "kwargs": kwargs})
                yield type("R", (), {"text": "A red pixel."})()

            with mock.patch("mlx_vlm.load", fake_vlm_load), \
                    mock.patch("mlx_vlm.stream_generate", fake_vlm_generate):
                messages = [
                    {"role": "user", "content": [
                        {"type": "text", "text": "What is this?"},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ]}
                ]
                outcome = engine.complete(messages, max_tokens=16)

            self.assertEqual(len(loaded_with), 1)
            self.assertEqual(loaded_with[0], str(model_dir))
            self.assertEqual(len(generated_with), 1)
            self.assertEqual(outcome["text"], "A red pixel.")
            self.assertEqual(len(generated_with[0]["image"]), 1)
            self.assertEqual(generated_with[0]["image"][0].size, (1, 1))
            self.assertIn("<|image|>", generated_with[0]["prompt"],
                          "the rendered prompt lost the image placeholder the processor expands")


if __name__ == "__main__":
    unittest.main()
