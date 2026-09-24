"""Single-model MLX chat engine for the standalone Chat worker.

Design rules, deliberately copied from the Image worker's shape:

* **One model, one lock.** MLX is a single Metal device; a second resident text model is a
  memory spike the owner's 16 GB Macs cannot absorb. Every load/generate/release happens under
  one lock.
* **Lazy load, honest release.** Nothing is loaded until the first request. ``release()`` drops
  the model and clears the MLX cache, and refuses while a generation is running rather than
  reporting a success it did not achieve.
* **No invented numbers.** Context window and output ceiling are read from the model's own
  ``config.json``/``generation_config.json`` when present; otherwise they are left unknown
  rather than guessed.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Iterator

# Conservative default when the model publishes nothing. 4,096 is the floor the app's own
# long-form budgeting already assumes, not a claim about this model.
FALLBACK_MAX_OUTPUT_TOKENS = 4096


def _mlx():
    import mlx.core as mx

    return mx


def _active_memory_bytes() -> int | None:
    """Active MLX allocation, tolerant of the API moving between MLX versions."""
    try:
        mx = _mlx()
        for owner in (mx, getattr(mx, "metal", None)):
            getter = getattr(owner, "get_active_memory", None)
            if callable(getter):
                return int(getter())
    except Exception:
        pass
    return None


def _peak_memory_bytes() -> int | None:
    try:
        mx = _mlx()
        for owner in (mx, getattr(mx, "metal", None)):
            getter = getattr(owner, "get_peak_memory", None)
            if callable(getter):
                return int(getter())
    except Exception:
        pass
    return None


class ChatModelUnavailable(RuntimeError):
    """The pinned model files are not present or not loadable."""


class ChatBusy(RuntimeError):
    """A generation is in flight; the request cannot be served or the model released."""


def resolve_model_dir(models_dir: Path, package_id: str, revision: str) -> Path:
    """The library layout every Studio uses: <models>/<package_id>/<revision>/model."""
    return Path(models_dir).expanduser() / package_id / revision / "model"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


class ChatEngine:
    def __init__(self, *, model_dir: Path, model_id: str, revision: str,
                 max_output_tokens: int | None = None,
                 context_window_tokens: int | None = None) -> None:
        self.model_dir = Path(model_dir)
        self.model_id = model_id
        self.revision = revision
        self._lock = threading.RLock()
        self._model = None
        self._tokenizer = None
        self._loaded_at: float | None = None
        self._last_release_at: float | None = None
        self._last_release_reason: str | None = None
        self._busy = False
        self._generating = False
        self._last_activity_at: float | None = None

        config = _read_json(self.model_dir / "config.json")
        generation = _read_json(self.model_dir / "generation_config.json")
        # Qwen3.5-style checkpoints nest the language model under ``text_config``; a
        # top-level field is also accepted for simpler repos.
        nested = config.get("text_config") if isinstance(config.get("text_config"), dict) else {}
        published_context = (config.get("max_position_embeddings") or config.get("context_length")
                             or nested.get("max_position_embeddings") or nested.get("context_length"))
        if context_window_tokens is not None:
            self.context_window_tokens = int(context_window_tokens)
        elif isinstance(published_context, int) and published_context > 0:
            self.context_window_tokens = published_context
        else:
            self.context_window_tokens = None
        published_output = generation.get("max_new_tokens") or generation.get("max_tokens")
        if max_output_tokens is not None:
            self.max_output_tokens = int(max_output_tokens)
        elif isinstance(published_output, int) and published_output > 0:
            self.max_output_tokens = published_output
        else:
            self.max_output_tokens = FALLBACK_MAX_OUTPUT_TOKENS

    # ---- readiness -------------------------------------------------------
    def files_present(self) -> bool:
        if not self.model_dir.is_dir():
            return False
        has_config = (self.model_dir / "config.json").is_file()
        has_weights = any(self.model_dir.glob("*.safetensors"))
        has_tokenizer = any((self.model_dir / name).is_file() for name in
                            ("tokenizer.json", "tokenizer_config.json", "vocab.json"))
        return has_config and has_weights and has_tokenizer

    def preflight(self) -> dict:
        if not self.model_dir.is_dir():
            return {"state": "missing", "model_dir": str(self.model_dir)}
        if not self.files_present():
            return {"state": "incomplete", "model_dir": str(self.model_dir)}
        return {"state": "files_verified", "model_dir": str(self.model_dir)}

    # ---- residency -------------------------------------------------------
    def loaded(self) -> bool:
        return self._model is not None

    def last_activity_at(self) -> float | None:
        return self._last_activity_at

    def _load_locked(self):
        if self._model is not None:
            return self._model, self._tokenizer
        if not self.files_present():
            raise ChatModelUnavailable(f"model files are not installed at {self.model_dir}")
        from mlx_lm import load

        started = time.time()
        model, tokenizer = load(str(self.model_dir))
        self._model, self._tokenizer = model, tokenizer
        self._loaded_at = time.time()
        self._last_release_reason = None
        self.last_load_seconds = round(self._loaded_at - started, 3)
        return model, tokenizer

    def release(self, *, reason: str = "requested", force: bool = False) -> dict:
        with self._lock:
            if self._generating and not force:
                raise ChatBusy("a generation is running; release was refused")
            was_loaded = self._model is not None
            self._model = None
            self._tokenizer = None
            self._loaded_at = None
            if was_loaded:
                try:
                    _mlx().clear_cache()
                except Exception:
                    pass
            self._last_release_at = time.time()
            self._last_release_reason = reason
            return {"released": was_loaded, "reason": reason}

    def status(self) -> dict:
        loaded = self.loaded()
        return {
            "model_id": self.model_id,
            "revision": self.revision,
            "loaded": loaded,
            "busy": self._generating,
            "loaded_at": self._loaded_at,
            "active_memory_bytes": _active_memory_bytes() if loaded else None,
            "peak_memory_bytes": _peak_memory_bytes() if loaded else None,
            "context_window_tokens": self.context_window_tokens,
            "max_output_tokens": self.max_output_tokens,
            "last_release_at": self._last_release_at,
            "last_release_reason": self._last_release_reason,
        }

    # ---- generation ------------------------------------------------------
    def count_tokens(self, text: str) -> int | None:
        """Tokenizer count for the loaded model, or ``None`` when nothing is loaded."""
        with self._lock:
            if self._tokenizer is None:
                return None
            try:
                return len(self._tokenizer.encode(text))
            except Exception:
                return None

    def prompt_length(self, messages: list[dict]) -> int | None:
        with self._lock:
            if self._tokenizer is None:
                return None
            return self.count_tokens(self._prompt(messages))

    def _prompt(self, messages: list[dict], *, thinking: bool = False) -> str:
        tokenizer = self._tokenizer
        apply = getattr(tokenizer, "apply_chat_template", None)
        if callable(apply):
            try:
                # Qwen3.5-9B emits a long "Thinking Process:" trace before the answer when the
                # template's default is left on, which also breaks JSON mode. Thinking is off by
                # default and can be requested per call.
                return apply(messages, tokenize=False, add_generation_prompt=True,
                             enable_thinking=thinking)
            except TypeError:
                # A tokenizer whose chat template takes no `enable_thinking` kwarg.
                return apply(messages, tokenize=False, add_generation_prompt=True)
        # Deterministic fallback for a tokenizer without a chat template.
        lines = [f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages]
        lines.append("assistant:")
        return "\n".join(lines)

    @staticmethod
    def json_instruction(response_format) -> str | None:
        """A prompt-level JSON contract for an engine without grammar-constrained decoding.

        mlx-lm exposes no schema/grammar sampler, so this is the best available: instruct,
        then validate and make one bounded repair attempt. It is not a guarantee — the caller
        (Story Studio) still validates — but it removes the common "fences and a sentence of
        commentary around the object" failure.
        """
        if not isinstance(response_format, dict):
            return None
        kind = str(response_format.get("type") or "")
        if kind == "json_object":
            return ("Return only one valid JSON object. No markdown fences, no commentary and no "
                    "text before or after the object.")
        if kind == "json_schema":
            schema = response_format.get("json_schema") or response_format.get("schema")
            return ("Return only one valid JSON object that validates against this JSON Schema. "
                    "No markdown fences and no commentary.\nJSON Schema:\n"
                    + json.dumps(schema, ensure_ascii=False))
        return None

    @staticmethod
    def _with_instruction(messages: list[dict], instruction: str | None) -> list[dict]:
        if not instruction:
            return messages
        return [{"role": "system", "content": instruction}, *messages]

    @staticmethod
    def _valid_json(text: str) -> bool:
        try:
            json.loads(text)
        except (TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _apply_seed(seed) -> None:
        """Honour an OpenAI-style seed where mlx supports one; never fail a request over it."""
        if seed is None:
            return
        try:
            import mlx.core as mx

            mx.random.seed(int(seed))
        except (TypeError, ValueError, ImportError, AttributeError):
            pass

    def complete(self, messages: list[dict], *, max_tokens: int | None = None,
                 temperature: float = 0.7, top_p: float = 0.95,
                 response_format=None, seed=None, thinking: bool = False) -> dict:
        with self._lock:
            model, tokenizer = self._load_locked()
            budget = min(int(max_tokens or self.max_output_tokens), self.max_output_tokens)
            instruction = self.json_instruction(response_format)
            working = self._with_instruction(messages, instruction)
            prompt_tokens = self.count_tokens(self._prompt(working, thinking=thinking))
            self._generating = True
            started = time.time()
            try:
                from mlx_lm import generate
                from mlx_lm.sample_utils import make_sampler

                # mlx-lm 0.31 asks for a sampler, not raw `temp`/`top_p`: its
                # `generate_step` has no such parameters and would raise TypeError.
                sampler = make_sampler(temp=temperature, top_p=top_p)
                self._apply_seed(seed)
                text = generate(model, tokenizer, prompt=self._prompt(working, thinking=thinking),
                                max_tokens=budget, sampler=sampler)
                json_valid = None
                repairs = 0
                if instruction:
                    json_valid = self._valid_json(text)
                    if not json_valid:
                        repairs = 1
                        correction = [
                            *working,
                            {"role": "assistant", "content": text},
                            {"role": "user", "content": "That reply was not valid JSON. Return "
                             "only the JSON object, with no fences and no commentary."},
                        ]
                        text = generate(model, tokenizer, prompt=self._prompt(correction, thinking=thinking),
                                        max_tokens=budget, sampler=sampler)
                        json_valid = self._valid_json(text)
            finally:
                self._generating = False
                self._last_activity_at = time.time()
            return {
                "text": text,
                "elapsed_seconds": round(time.time() - started, 3),
                "max_tokens": budget,
                "prompt_tokens": prompt_tokens,
                "json_valid": json_valid,
                "repairs": repairs,
            }

    def stream(self, messages: list[dict], *, max_tokens: int | None = None,
               temperature: float = 0.7, top_p: float = 0.95,
               response_format=None, seed=None, thinking: bool = False) -> Iterator[str]:
        with self._lock:
            model, tokenizer = self._load_locked()
            budget = min(int(max_tokens or self.max_output_tokens), self.max_output_tokens)
            working = self._with_instruction(messages, self.json_instruction(response_format))
            self._generating = True
            self._last_activity_at = time.time()
            try:
                from mlx_lm import stream_generate
                from mlx_lm.sample_utils import make_sampler

                sampler = make_sampler(temp=temperature, top_p=top_p)
                self._apply_seed(seed)
                for response in stream_generate(model, tokenizer, prompt=self._prompt(working, thinking=thinking),
                                                max_tokens=budget, sampler=sampler):
                    self._last_activity_at = time.time()
                    yield getattr(response, "text", "")
            finally:
                self._generating = False
                self._last_activity_at = time.time()


def engine_from_environment() -> ChatEngine:
    """Construct the engine from the environment the packaged worker exports."""
    models_dir = os.environ.get("STUDIOHUB_MODELS_DIR")
    manifest_path = os.environ.get("STUDIOHUB_CHAT_MANIFEST")
    if not models_dir or not manifest_path:
        raise ChatModelUnavailable("STUDIOHUB_MODELS_DIR and STUDIOHUB_CHAT_MANIFEST are required")
    manifest = json.loads(Path(manifest_path).read_text())
    package_id = manifest["package_id"]
    revision = manifest["revision"]
    model_dir = resolve_model_dir(Path(models_dir), package_id, revision)
    qualification = manifest.get("qualification") or {}
    return ChatEngine(
        model_dir=model_dir,
        model_id=manifest["model_id"],
        revision=revision,
        max_output_tokens=manifest.get("max_output_tokens"),
        context_window_tokens=qualification.get("context_window_tokens"),
    )
