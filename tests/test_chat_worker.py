"""Local Chat Studio worker — engine, memory policy and API contract.

Runs entirely without model weights: the engine's filesystem/telemetry paths and the
FastAPI contract are exercised with a stub engine, so this suite is valid on any host and
does not pretend to be the missing packaged-inference proof.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from components.chat.app.backend import memory_policy as memory_policy_module
from components.chat.app.backend.llm_engine import (ChatBusy, ChatEngine,
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
        (model_dir / "config.json").write_text(
            json.dumps({"text_config": {"max_position_embeddings": 4096}}))
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
            self.assertEqual(engine.context_window_tokens, 4096)

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


if __name__ == "__main__":
    unittest.main()
