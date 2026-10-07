"""JSON mode + a caller's system message: one system turn, and a typed error when a template refuses.

Regression for the 2026-10-02 benchmark finding: `ChatEngine` prepended its own JSON-contract system
message, so a caller that also sent `system` produced two, and the Qwen3.5 / Gemma chat templates
raise `TemplateError` for that. The fake tokenizer below enforces the same two rules; one test
renders the real Qwen3.5 template from the local snapshot when it is present.
"""
from __future__ import annotations

import glob
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from components.chat.app.backend import memory_policy as memory_policy_module
from components.chat.app.backend.llm_engine import ChatEngine, ChatTemplateError
from components.chat.app.backend.main import create_app

TOKEN = "test-owner-token"
JSON_OBJECT = {"type": "json_object"}
SNAPSHOT = glob.glob(os.path.expanduser("~/StudioHubModels/qwen3-5-9b-4bit/*/model"))


_MISSING = object()
_ORIGINAL_MLX_MODULES = {}


def setUpModule() -> None:
    """Keep these template tests fake-only; the optional real test loads only a tokenizer."""
    mlx_lm = types.ModuleType("mlx_lm")
    mlx_lm.__path__ = []

    def not_stubbed(*_args, **_kwargs):  # pragma: no cover - names a broken fixture
        raise AssertionError("test reached an unstubbed fake mlx_lm function")

    mlx_lm.load = not_stubbed
    mlx_lm.stream_generate = not_stubbed
    sample_utils = types.ModuleType("mlx_lm.sample_utils")
    sample_utils.make_sampler = lambda **kwargs: ("sampler", kwargs)
    mlx_lm.sample_utils = sample_utils
    mlx = types.ModuleType("mlx")
    mlx.__path__ = []
    core = types.ModuleType("mlx.core")
    core.random = types.SimpleNamespace(seed=lambda _seed: None)
    core.clear_cache = lambda: None
    mlx.core = core
    replacements = {
        "mlx_lm": mlx_lm,
        "mlx_lm.sample_utils": sample_utils,
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


def _response(text: str):
    return types.SimpleNamespace(
        text=text, prompt_tokens=4, prompt_tps=40.0,
        generation_tokens=2, generation_tps=8.0,
        peak_memory=0.5, finish_reason="stop")


def _stream_text(generate):
    def stream(*args, **kwargs):
        progress = kwargs.pop("prompt_progress_callback", None)
        if progress is not None:
            progress(4, 4)
        yield _response(generate(*args, **kwargs))
    return stream


class TemplateError(Exception):
    """Same name and base the real `jinja2.exceptions.TemplateError` has."""


class StrictTokenizer:
    """Qwen3.5's rule ("System message must be at the beginning") and Gemma's alternation rule."""

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True,
                            enable_thinking=False):
        for index, message in enumerate(messages):
            if message["role"] == "system" and index != 0:
                raise TemplateError("System message must be at the beginning.")
        if not any(m["role"] == "user" for m in messages):
            raise TemplateError("No user query found in messages.")
        return "".join(f"<|{m['role']}|>{m['content']}" for m in messages) + "<|assistant|>"

    def encode(self, text):
        return text.split()


def _engine(root: Path) -> ChatEngine:
    model_dir = root / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text(
        json.dumps({"text_config": {"max_position_embeddings": 65536}}))
    (model_dir / "model.safetensors").write_bytes(b"weights")
    (model_dir / "tokenizer.json").write_text("{}")
    return ChatEngine(model_dir=model_dir, model_id="m", revision="r" * 40)


def _msg(role, content):
    return {"role": role, "content": content}


class MergeSystemTests(unittest.TestCase):
    instruction = ChatEngine.json_instruction(JSON_OBJECT)

    def test_instruction_joins_the_callers_leading_system_message(self) -> None:
        out = ChatEngine._with_instruction([_msg("system", "Plan scenes."), _msg("user", "go")],
                                           self.instruction)
        self.assertEqual([m["role"] for m in out], ["system", "user"])
        self.assertTrue(out[0]["content"].startswith("Plan scenes.\n\n"))
        self.assertTrue(out[0]["content"].endswith(self.instruction))

    def test_instruction_is_inserted_only_when_there_is_no_system_message(self) -> None:
        out = ChatEngine._with_instruction([_msg("user", "go")], self.instruction)
        self.assertEqual(out, [_msg("system", self.instruction), _msg("user", "go")])

    def test_several_and_late_system_messages_collapse_into_one_leading_message(self) -> None:
        conversation = [_msg("system", "A"), _msg("user", "u1"), _msg("system", "B"),
                        _msg("assistant", "a1"), _msg("user", "u2"), _msg("system", "  ")]
        out = ChatEngine._with_instruction(conversation, self.instruction)
        self.assertEqual([m["role"] for m in out], ["system", "user", "assistant", "user"])
        self.assertEqual(out[0]["content"], f"A\n\nB\n\n{self.instruction}")
        self.assertEqual([m["content"] for m in out[1:]], ["u1", "a1", "u2"])
        # Without a JSON request the same collapse still happens, so the template never sees them.
        plain = ChatEngine._with_instruction(conversation, None)
        self.assertEqual(plain[0], _msg("system", "A\n\nB"))

    def test_a_well_formed_conversation_is_returned_untouched(self) -> None:
        for conversation in ([_msg("user", "u")], [_msg("system", "S"), _msg("user", "u")]):
            self.assertIs(ChatEngine._with_instruction(conversation, None), conversation)

    def test_callers_messages_are_not_mutated(self) -> None:
        conversation = [_msg("system", "A"), _msg("user", "u")]
        ChatEngine._with_instruction(conversation, self.instruction)
        self.assertEqual(conversation, [_msg("system", "A"), _msg("user", "u")])


class EngineJsonSystemTests(unittest.TestCase):
    def test_json_request_with_a_system_message_renders_on_a_strict_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine(Path(tmp))
            prompts: list[str] = []

            def fake_generate(model, tokenizer, prompt, **kwargs):
                prompts.append(prompt)
                return '{"ok": true}'

            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), StrictTokenizer())), \
                    mock.patch("mlx_lm.stream_generate", _stream_text(fake_generate)):
                outcome = engine.complete(
                    [_msg("system", "You plan scenes."), _msg("user", "go")],
                    response_format=JSON_OBJECT)
        self.assertTrue(outcome["json_valid"])
        self.assertEqual(prompts[0].count("<|system|>"), 1)
        self.assertIn("You plan scenes.", prompts[0])
        self.assertIn("Return only one valid JSON object", prompts[0])

    def test_repair_turn_keeps_a_single_system_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine(Path(tmp))
            replies = iter(["not json", '{"a": 1}'])
            prompts: list[str] = []

            def fake_generate(model, tokenizer, prompt, **kwargs):
                prompts.append(prompt)
                return next(replies)

            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), StrictTokenizer())), \
                    mock.patch("mlx_lm.stream_generate", _stream_text(fake_generate)):
                outcome = engine.complete([_msg("system", "S"), _msg("user", "go")],
                                          response_format=JSON_OBJECT)
        self.assertEqual(outcome["repairs"], 1)
        self.assertEqual(prompts[1].count("<|system|>"), 1)

    def test_template_refusal_is_a_typed_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine(Path(tmp))
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), StrictTokenizer())):
                with self.assertRaises(ChatTemplateError) as caught:
                    engine.complete([_msg("system", "only a system message")])
        self.assertIn("No user query found", str(caught.exception))
        self.assertTrue(str(caught.exception).startswith("messages:"))

    def test_other_tokenizer_failures_are_not_relabelled(self) -> None:
        class Broken(StrictTokenizer):
            def apply_chat_template(self, *args, **kwargs):
                raise KeyError("boom")

        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine(Path(tmp))
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), Broken())):
                with self.assertRaises(KeyError):
                    engine.complete([_msg("user", "hi")])


class RouteTemplateErrorTests(unittest.TestCase):
    def _post(self, tmp: str, body: dict):
        engine = _engine(Path(tmp))
        app = create_app(engine, memory_policy_module.MemoryPolicy(Path(tmp), engine),
                         TOKEN, "b" * 64)
        with mock.patch("mlx_lm.load", lambda *a, **k: (object(), StrictTokenizer())), \
                mock.patch("mlx_lm.stream_generate",
                           _stream_text(lambda *a, **k: '{"x": 1}')), \
                TestClient(app) as client:
            return client.post("/v1/chat/completions", headers={"x-studio-token": TOKEN},
                               json=body)

    def test_json_plus_system_now_answers_200(self) -> None:
        body = {"messages": [_msg("system", "S"), _msg("user", "go")],
                "response_format": JSON_OBJECT}
        with tempfile.TemporaryDirectory() as tmp:
            response = self._post(tmp, {**body, "stream": False})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["choices"][0]["message"]["content"], '{"x": 1}')
        with tempfile.TemporaryDirectory() as tmp:
            response = self._post(tmp, {**body, "stream": True})
            self.assertEqual(response.status_code, 200)
            chunks = [json.loads(line[6:]) for line in response.text.splitlines()
                      if line.startswith("data: {")]
            self.assertEqual(chunks[0]["choices"][0]["delta"]["content"], '{"x": 1}')

    def test_structured_stream_repairs_before_emitting_and_uses_final_finish_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine(Path(tmp))
            replies = iter(["not json", '{"x": 1}'])
            reasons = iter(["length", "stop"])

            def stream(*args, **kwargs):
                progress = kwargs.pop("prompt_progress_callback", None)
                if progress:
                    progress(4, 4)
                yield types.SimpleNamespace(
                    text=next(replies), prompt_tokens=4, prompt_tps=40.0,
                    generation_tokens=2, generation_tps=8.0, peak_memory=0.5,
                    finish_reason=next(reasons))

            app = create_app(engine, memory_policy_module.MemoryPolicy(Path(tmp), engine),
                             TOKEN, "b" * 64)
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), StrictTokenizer())), \
                    mock.patch("mlx_lm.stream_generate", stream), TestClient(app) as client:
                response = client.post("/v1/chat/completions",
                    headers={"x-studio-token": TOKEN}, json={
                        "messages": [_msg("user", "go")], "response_format": JSON_OBJECT,
                        "stream": True,
                    })
                contract = client.get("/api/catalog", headers={
                    "x-studio-token": TOKEN}).json()["models"][0]["lane_contract"]
        self.assertEqual(response.status_code, 200, response.text)
        chunks = [json.loads(line[6:]) for line in response.text.splitlines()
                  if line.startswith("data: {")]
        self.assertEqual(chunks[0]["choices"][0]["delta"]["content"], '{"x": 1}')
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")
        self.assertEqual(contract["structured_output"]["streaming"],
                         "buffered_until_validated")

    def test_structured_stream_refuses_two_invalid_attempts_before_http_200(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine(Path(tmp))
            app = create_app(engine, memory_policy_module.MemoryPolicy(Path(tmp), engine),
                             TOKEN, "b" * 64)
            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), StrictTokenizer())), \
                    mock.patch("mlx_lm.stream_generate",
                               _stream_text(lambda *a, **k: "invalid")), \
                    TestClient(app) as client:
                response = client.post("/v1/chat/completions",
                    headers={"x-studio-token": TOKEN}, json={
                        "messages": [_msg("user", "go")], "response_format": JSON_OBJECT,
                        "stream": True,
                    })
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("structured output", response.json()["detail"])

    def test_template_refusal_is_a_422_that_names_the_reason(self) -> None:
        for stream in (False, True):
            with self.subTest(stream=stream), tempfile.TemporaryDirectory() as tmp:
                response = self._post(tmp, {"messages": [_msg("system", "S")], "stream": stream})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("chat template refused", response.json()["detail"])
                self.assertIn("No user query found", response.json()["detail"])


@unittest.skipUnless(SNAPSHOT, "the Qwen3.5 snapshot is not in ~/StudioHubModels")
class RealQwenTemplateTests(unittest.TestCase):
    """The real chat template, from the local snapshot, offline."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            with mock.patch.dict(os.environ, {"HF_HUB_OFFLINE": "1"}):
                from transformers import AutoTokenizer
                cls.tokenizer = AutoTokenizer.from_pretrained(SNAPSHOT[0])
        except Exception as exc:  # no transformers in this interpreter
            raise unittest.SkipTest(f"cannot load the snapshot tokenizer: {exc}")

    def test_the_real_template_refuses_what_the_engine_used_to_send(self) -> None:
        with self.assertRaises(Exception) as caught:
            self.tokenizer.apply_chat_template(
                [_msg("system", "A"), _msg("system", "B"), _msg("user", "u")],
                tokenize=False, add_generation_prompt=True, enable_thinking=False)
        self.assertIn("System message must be at the beginning", str(caught.exception))

    def test_json_plus_system_renders_one_system_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine(Path(tmp))
            prompts: list[str] = []

            def fake_generate(model, tokenizer, prompt, **kwargs):
                prompts.append(prompt)
                return '{"scenes": []}'

            with mock.patch("mlx_lm.load", lambda *a, **k: (object(), self.tokenizer)), \
                    mock.patch("mlx_lm.stream_generate", _stream_text(fake_generate)):
                engine.complete(
                    [_msg("system", "You plan scenes."), _msg("user", "Plan it."),
                     _msg("system", "late note")],
                    response_format=JSON_OBJECT)
                with self.assertRaises(ChatTemplateError):
                    engine.complete([_msg("system", "no user turn")])
        prompt = prompts[0]
        self.assertEqual(prompt.count("<|im_start|>system"), 1)
        for text in ("You plan scenes.", "late note", "Return only one valid JSON object"):
            self.assertIn(text, prompt)
        self.assertLess(prompt.index("<|im_start|>system"), prompt.index("<|im_start|>user"))


if __name__ == "__main__":
    unittest.main()
