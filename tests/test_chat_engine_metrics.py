"""Fake-only checks for ChatEngine's request-scoped MLX telemetry."""
from __future__ import annotations

import math
import os
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from components.chat.app.backend.llm_engine import ChatEngine, ChatStructuredOutputError


_MISSING = object()
_ORIGINAL_MLX_MODULES = {}


def setUpModule() -> None:
    """Install fake MLX before any engine helper can ask for cache controls."""
    mlx = types.ModuleType("mlx")
    mlx.__path__ = []
    core = types.ModuleType("mlx.core")
    core.random = types.SimpleNamespace(seed=lambda _seed: None)
    core.clear_cache = lambda: None
    core.set_cache_limit = lambda _limit: None
    mlx.core = core
    for name, module in {"mlx": mlx, "mlx.core": core}.items():
        _ORIGINAL_MLX_MODULES[name] = sys.modules.get(name, _MISSING)
        sys.modules[name] = module


def tearDownModule() -> None:
    for name, module in _ORIGINAL_MLX_MODULES.items():
        if module is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class _Tokenizer:
    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True,
                            enable_thinking=False):
        return "<prompt>"

    def encode(self, text):
        return [1, 2, 3]


def _response(text: str, **metrics):
    return types.SimpleNamespace(text=text, **metrics)


@contextmanager
def _fake_mlx_lm(stream_generate, load=None):
    """Install only fakes; importing real MLX in this module is a test failure."""
    mlx_lm = types.ModuleType("mlx_lm")
    mlx_lm.load = load or (lambda *_args, **_kwargs: (object(), _Tokenizer()))
    mlx_lm.stream_generate = stream_generate
    sample_utils = types.ModuleType("mlx_lm.sample_utils")
    sample_utils.make_sampler = lambda **kwargs: ("sampler", kwargs)
    mlx_lm.sample_utils = sample_utils
    with mock.patch.dict(sys.modules, {
        "mlx_lm": mlx_lm,
        "mlx_lm.sample_utils": sample_utils,
    }):
        yield


class ChatEngineMetricsTests(unittest.TestCase):
    MESSAGE = [{"role": "user", "content": "hi"}]

    def _engine(self, root: Path) -> ChatEngine:
        model_dir = root / "model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}")
        (model_dir / "model.safetensors").write_bytes(b"fake")
        (model_dir / "tokenizer.json").write_text("{}")
        return ChatEngine(model_dir=model_dir, model_id="fake/model-4bit", revision="fake")

    @staticmethod
    def _consume(stream):
        chunks = []
        while True:
            try:
                chunks.append(next(stream))
            except StopIteration as stop:
                return chunks, stop.value

    def test_default_cache_cap_is_applied_before_weights_load(self):
        order = []

        def load(*_args, **_kwargs):
            order.append("load")
            return object(), _Tokenizer()

        def generate(*_args, **_kwargs):
            yield _response("ok")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch("mlx.core.set_cache_limit",
                           side_effect=lambda value: order.append(("cache", value))), \
                _fake_mlx_lm(generate, load=load):
            os.environ.pop("STUDIOHUB_MLX_CACHE_LIMIT_MB", None)
            self._engine(Path(tmp)).complete(self.MESSAGE)

        self.assertEqual(order, [("cache", 512_000_000), "load"])

    def test_nan_cache_override_does_not_block_or_set_the_cache(self):
        order = []

        def load(*_args, **_kwargs):
            order.append("load")
            return object(), _Tokenizer()

        def generate(*_args, **_kwargs):
            yield _response("ok")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"STUDIOHUB_MLX_CACHE_LIMIT_MB": "nan"}), \
                mock.patch("mlx.core.set_cache_limit",
                           side_effect=lambda value: order.append(("cache", value))), \
                _fake_mlx_lm(generate, load=load):
            self._engine(Path(tmp)).complete(self.MESSAGE)

        self.assertEqual(order, ["load"])

    def test_complete_reports_mlx_prefill_and_decode_metrics_without_mixing_them(self):
        events = []

        def generate(*_args, prompt_progress_callback, **_kwargs):
            prompt_progress_callback(4, 12)
            yield _response(
                "Hello ", prompt_tokens=12, prompt_tps=120.5,
                generation_tokens=1, generation_tps=5.25,
                peak_memory=1.25, finish_reason=None)
            prompt_progress_callback(12, 12)
            yield _response(
                "world", prompt_tokens=12, prompt_tps=120.5,
                generation_tokens=2, generation_tps=6.5,
                peak_memory=1.5, finish_reason="stop")

        with tempfile.TemporaryDirectory() as tmp, _fake_mlx_lm(generate):
            outcome = self._engine(Path(tmp)).complete(
                self.MESSAGE, max_tokens=8, on_progress=events.append)

        self.assertEqual(outcome["text"], "Hello world")
        self.assertEqual(outcome["prompt_tokens"], 3,
                         "the legacy tokenizer count must keep its meaning")
        self.assertEqual(outcome["telemetry"], {
            "attempts": [{
                "attempt": 1,
                "prompt_tokens": 12,
                "prompt_tps": 120.5,
                "generation_tokens": 2,
                "generation_tps": 6.5,
                "peak_memory_bytes": 1_500_000_000,
                "finish_reason": "stop",
            }],
            "final_attempt": 1,
        })
        self.assertEqual(outcome["usage"], {
            "prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6,
        })
        self.assertEqual(events[-1]["usage"], outcome["usage"])
        processing = [event for event in events if event["phase"] == "processing"
                      and "processed_prompt_tokens" in event]
        self.assertEqual(processing, [
            {"phase": "processing", "attempt": 1,
             "processed_prompt_tokens": 4, "total_prompt_tokens": 12},
            {"phase": "processing", "attempt": 1,
             "processed_prompt_tokens": 12, "total_prompt_tokens": 12},
        ])
        generating = [event for event in events if event["phase"] == "generating"]
        self.assertEqual(generating[-1]["prompt_tps"], 120.5)
        self.assertEqual(generating[-1]["generation_tps"], 6.5)
        self.assertNotEqual(generating[-1]["prompt_tps"], generating[-1]["generation_tps"])
        self.assertEqual(events[-1], {
            "phase": "completed", "attempt": 1,
            "prompt_tokens": 12, "prompt_tps": 120.5,
            "generation_tokens": 2, "generation_tps": 6.5,
            "peak_memory_bytes": 1_500_000_000, "finish_reason": "stop",
            "telemetry": outcome["telemetry"], "usage": outcome["usage"],
        })

    def test_missing_empty_and_non_finite_response_metrics_remain_absent(self):
        events = []

        def generate(*_args, **_kwargs):
            yield _response(
                "ok", prompt_tokens=None, prompt_tps=math.nan,
                generation_tokens="", generation_tps=math.inf,
                peak_memory=None, finish_reason="")

        with tempfile.TemporaryDirectory() as tmp, _fake_mlx_lm(generate):
            outcome = self._engine(Path(tmp)).complete(
                self.MESSAGE, on_progress=events.append)

        self.assertEqual(outcome["telemetry"], {
            "attempts": [{"attempt": 1}],
            "final_attempt": 1,
        })
        self.assertEqual(events[-1], {
            "phase": "completed", "attempt": 1,
            "telemetry": outcome["telemetry"],
            "usage": outcome["usage"],
        })

    def test_usage_keeps_failed_tokenizer_counts_unknown_but_preserves_real_zero(self):
        class BrokenTokenizer(_Tokenizer):
            def encode(self, _text):
                raise RuntimeError("cannot count")

        class ZeroTokenizer(_Tokenizer):
            def encode(self, _text):
                return []

        def generate(*_args, **_kwargs):
            yield _response("ok")

        events = []
        with tempfile.TemporaryDirectory() as tmp, _fake_mlx_lm(
                generate, load=lambda *_a, **_k: (object(), BrokenTokenizer())):
            outcome = self._engine(Path(tmp)).complete(self.MESSAGE, on_progress=events.append)
        unknown = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
        self.assertEqual(outcome["usage"], unknown)
        self.assertEqual(events[-1]["usage"], unknown)

        events = []
        with tempfile.TemporaryDirectory() as tmp, _fake_mlx_lm(
                generate, load=lambda *_a, **_k: (object(), ZeroTokenizer())):
            outcome = self._engine(Path(tmp)).complete(self.MESSAGE, on_progress=events.append)
        zero = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.assertEqual(outcome["usage"], zero)
        self.assertEqual(events[-1]["usage"], zero)

    def test_json_repair_keeps_two_attempt_records_and_labels_the_final_attempt(self):
        events = []
        calls = 0

        def generate(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield _response("not json", prompt_tokens=10, prompt_tps=50.0,
                                generation_tokens=2, generation_tps=4.0,
                                peak_memory=1.0, finish_reason="stop")
            else:
                yield _response('{"ok": true}', prompt_tokens=18, prompt_tps=45.0,
                                generation_tokens=4, generation_tps=3.5,
                                peak_memory=1.1, finish_reason="stop")

        with tempfile.TemporaryDirectory() as tmp, _fake_mlx_lm(generate):
            outcome = self._engine(Path(tmp)).complete(
                self.MESSAGE, response_format={"type": "json_object"},
                on_progress=events.append)

        self.assertEqual(calls, 2)
        self.assertTrue(outcome["json_valid"])
        self.assertEqual(outcome["repairs"], 1)
        self.assertEqual(outcome["telemetry"]["final_attempt"], 2)
        self.assertEqual([row["attempt"] for row in outcome["telemetry"]["attempts"]], [1, 2])
        self.assertEqual(outcome["telemetry"]["attempts"][0]["generation_tokens"], 2)
        self.assertEqual(outcome["telemetry"]["attempts"][1]["generation_tokens"], 4)
        self.assertEqual(events[-1]["attempt"], 2)
        self.assertEqual(events[-1]["telemetry"], outcome["telemetry"])

    def test_type_error_is_not_retried_as_json_repair(self):
        calls = 0

        def generate(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise TypeError("bad generation arguments")
            yield  # pragma: no cover - makes this a generator without running real MLX

        with tempfile.TemporaryDirectory() as tmp, _fake_mlx_lm(generate):
            engine = self._engine(Path(tmp))
            with self.assertRaisesRegex(TypeError, "bad generation arguments"):
                engine.complete(self.MESSAGE, response_format={"type": "json_object"})
            self.assertFalse(engine.status()["busy"])

        self.assertEqual(calls, 1)

    def test_two_invalid_json_attempts_raise_a_named_failure(self):
        calls = 0

        def generate(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            yield _response("still not json", finish_reason="length")

        with tempfile.TemporaryDirectory() as tmp, _fake_mlx_lm(generate):
            with self.assertRaisesRegex(ChatStructuredOutputError, "valid JSON"):
                self._engine(Path(tmp)).complete(
                    self.MESSAGE, response_format={"type": "json_object"})
        self.assertEqual(calls, 2)

    def test_stream_keeps_string_deltas_float_return_and_measured_telemetry(self):
        events = []

        def generate(*_args, prompt_progress_callback, **_kwargs):
            prompt_progress_callback(7, 7)
            yield _response("a", prompt_tokens=7, prompt_tps=70.0,
                            generation_tokens=1, generation_tps=8.0,
                            peak_memory=0.5, finish_reason=None)
            yield _response("b", prompt_tokens=7, prompt_tps=70.0,
                            generation_tokens=2, generation_tps=9.0,
                            peak_memory=0.6, finish_reason="length")

        with tempfile.TemporaryDirectory() as tmp, _fake_mlx_lm(generate):
            engine = self._engine(Path(tmp))
            chunks, elapsed = self._consume(
                engine.stream(self.MESSAGE, max_tokens=2, on_progress=events.append))

        self.assertEqual(chunks, ["a", "b"])
        self.assertTrue(all(isinstance(chunk, str) for chunk in chunks))
        self.assertIsInstance(elapsed, float)
        self.assertEqual(events[-1]["telemetry"]["attempts"][0]["finish_reason"], "length")
        self.assertEqual(events[-1]["telemetry"]["attempts"][0]["peak_memory_bytes"],
                         600_000_000)
        self.assertEqual(events[-1]["usage"], {
            "prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6,
        })
        self.assertEqual((engine.status()["streams_started"],
                          engine.status()["streams_finished"]), (1, 1))

    def test_closing_stream_balances_lock_without_claiming_completion(self):
        events = []

        def generate(*_args, **_kwargs):
            yield _response("first", generation_tokens=1, generation_tps=2.0)
            yield _response("never delivered", generation_tokens=2, generation_tps=2.0)

        with tempfile.TemporaryDirectory() as tmp, _fake_mlx_lm(generate):
            engine = self._engine(Path(tmp))
            stream = engine.stream(self.MESSAGE, on_progress=events.append)
            self.assertEqual(next(stream), "first")
            stream.close()

        self.assertFalse(any(event["phase"] == "completed" for event in events))
        self.assertFalse(engine.status()["busy"])
        self.assertEqual((engine.status()["streams_started"],
                          engine.status()["streams_finished"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
