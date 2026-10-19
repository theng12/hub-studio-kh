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
import math
import os
import threading
import time
from pathlib import Path
from typing import Callable, Generator

# Conservative default when the model publishes nothing. It is the app's own long-form budgeting
# floor, **not a claim about this model** — Qwen3.5-9B 4-bit's own `config.json` states
# `max_position_embeddings = 262144`, and this number is an order of magnitude below it.
#
# Raised 4,096 -> 16,384 on 2026-09-26 and 16,384 -> **128,000 on 2026-10-19**, the owner's
# decision: *"4096 is too small. all models write more than that. the minimum is at least 128K
# tokens."* It is the last resort only — a figure the model states always wins, and a caller's
# number always wins over both.
#
# **What this does to the chat deadline, plainly.** The Hub's own floor is
# `standalone.MODALITY_DEADLINE_FLOOR_S["chat"] = 1800.0`, and the pair was tuned together on
# measured throughput: 16,384 tokens in 1,800 s is 76 / 65 / 51 % on M1 / M2 / M4 (≈ 12.0 / 14.0
# / 17.9 tokens a second; the same arithmetic that set the 1,800 s floor from 12,090 tokens in
# 1,008 s on an M1). At those rates **1,800 s now buys about 21,500 (M1) / 25,200 (M2) / 32,100
# (M4) tokens — a sixth to a quarter of the allowance this constant publishes. A caller that
# genuinely generates 128,000 tokens is therefore cut by the DEADLINE, not by the ceiling,
# roughly 2.0-3.0 hours before it could finish** (10,700 s on an M1, 7,200 s on an M4).
#
# That is stated rather than fixed, deliberately. Raising this constant does not by itself make
# an answer longer: a chat answer stops at EOS, and the ceiling is a maximum, not a target — it
# removes a cap, it does not add work. Raising the deadline would, and it is a different
# decision with a different cost: a lane is held for hours and a broken job is noticed in hours
# instead of 30 minutes (the trade the owner weighed and accepted at 1,800 s: *"every job may
# hang 30 min instead of 15"*). `stall.py` still catches a worker that has gone quiet in about
# two minutes whatever this number is. So the two numbers no longer agree, and the floor is the
# half left alone — **whoever raises it next must re-derive it from this arithmetic, in the same
# build, or the lane publishes a capacity it cannot reach before its own deadline.**
#
# The default is also clamped to the room the model's own context leaves
# (`_refuse_over_context(asked=False)`), so a model with a smaller window than this figure —
# gemma4-26b-a4b-qat-4bit states 32,768 — is not refused for a number it never chose.
FALLBACK_MAX_OUTPUT_TOKENS = 128_000

# This worker's sampling defaults. They live here, not in a route literal, so the value the
# lane contract publishes and the value a request is served with cannot drift apart.
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TOP_P = 0.95


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


def _bound_mlx_cache() -> None:
    """Use the shared MLX cache bound from source checkout or staged app layout."""
    try:
        from components.voice.app.backend.mlx_memory import bound_cache
    except ModuleNotFoundError:
        # The app stages Voice at Resources/voice and Chat at Resources/components/chat.
        from voice.app.backend.mlx_memory import bound_cache
    bound_cache()


class ChatModelUnavailable(RuntimeError):
    """The pinned model files are not present or not loadable."""


class ChatBusy(RuntimeError):
    """A generation is in flight; the request cannot be served or the model released."""


class ChatContextExceeded(ValueError):
    """The prompt plus the requested output does not fit the context the model states.

    A refusal, never a truncation: the message names the field (``messages``), the measured
    prompt tokens, the asked-for output budget and the model's own stated context with its
    source.
    """


class ChatTemplateError(ValueError):
    """The model's chat template refused the conversation.

    Chat templates (Qwen3.5, Gemma 3) raise on shapes they cannot render - a system message that is
    not first, roles that do not alternate, no user turn. That is a fault in the request, not in the
    worker, so it is a typed refusal that quotes the template's own reason (route: 422) instead of
    escaping as an anonymous 500.
    """


class ChatStructuredOutputError(ValueError):
    """The bounded structured-output attempts did not produce valid JSON."""


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
                 context_window_tokens: int | None = None,
                 manifest: dict | None = None,
                 manifest_path: Path | None = None,
                 minimum_unified_memory_gb: float | None = None) -> None:
        self.model_dir = Path(model_dir)
        self.model_id = model_id
        self.revision = revision
        # The model manifest this worker was pointed at, and where it came from. Both are the
        # model's own facts: the catalog row's label, its family and its memory tier are read
        # from here rather than written into this worker, so re-pointing the worker at another
        # manifest cannot publish the previous model's name.
        self.manifest = dict(manifest or {})
        self.manifest_path = Path(manifest_path) if manifest_path else None
        # The tier the model's own manifest qualifies it at, under either spelling. `None` when
        # the manifest publishes none: the worker must not invent a memory tier.
        self.minimum_unified_memory_gb = (
            minimum_unified_memory_gb
            if minimum_unified_memory_gb is not None
            else self._manifest_memory_tier()
        )
        # A plain lock, not an RLock: a stream may be closed by whichever thread finalises its
        # generator (Starlette runs a route generator in a worker thread, and cancellation can
        # land on the event loop), and an RLock can only be released by the thread that took it.
        # Nothing here nests an acquire: the helpers that count tokens while a caller holds the
        # lock take the tokenizer reference rather than the lock.
        self._lock = threading.Lock()
        self._model = None
        self._tokenizer = None
        self._loaded_at: float | None = None
        self._last_release_at: float | None = None
        self._last_release_reason: str | None = None
        self._busy = False
        self._generating = False
        self._last_activity_at: float | None = None
        # Streams that began and streams that ended, counted under the lock. They balance for
        # every ending - completed, errored, cancelled, client gone - and a mismatch is how a
        # wedged model lock is detected from outside the engine.
        self._streams_started = 0
        self._streams_finished = 0

        config = _read_json(self.model_dir / "config.json")
        generation_path = self.model_dir / "generation_config.json"
        generation = _read_json(generation_path)
        self._config = config
        # Qwen3.5-style checkpoints nest the language model under ``text_config``; a
        # top-level field is also accepted for simpler repos.
        nested = config.get("text_config") if isinstance(config.get("text_config"), dict) else {}
        published_context = (config.get("max_position_embeddings") or config.get("context_length")
                             or nested.get("max_position_embeddings") or nested.get("context_length"))
        if context_window_tokens is not None:
            self.context_window_tokens = int(context_window_tokens)
            self.context_window_source = (
                "the model manifest's qualification.context_window_tokens")
            self.context_window_source_kind = "curated"
        elif isinstance(published_context, int) and published_context > 0:
            self.context_window_tokens = published_context
            # Say which file and key the number came from, so a reader never has to guess and
            # never inherits it as a fact without provenance.
            self.context_window_source_kind = "model"
            if config.get("max_position_embeddings") == published_context:
                self.context_window_source = "model/config.json max_position_embeddings"
            elif config.get("context_length") == published_context:
                self.context_window_source = "model/config.json context_length"
            elif nested.get("max_position_embeddings") == published_context:
                self.context_window_source = (
                    "model/config.json text_config.max_position_embeddings")
            else:
                self.context_window_source = "model/config.json text_config.context_length"
        else:
            self.context_window_tokens = None
            self.context_window_source_kind = "unknown"
            self.context_window_source = (
                "nothing states a context window: model/config.json has no "
                "max_position_embeddings or context_length")
        published_output = generation.get("max_new_tokens") or generation.get("max_tokens")
        if max_output_tokens is not None:
            self.max_output_tokens = int(max_output_tokens)
            self.max_output_tokens_source = "the model manifest's max_output_tokens"
            self.max_output_tokens_source_kind = "curated"
        elif isinstance(published_output, int) and published_output > 0:
            self.max_output_tokens = published_output
            self.max_output_tokens_source_kind = "model"
            self.max_output_tokens_source = (
                "model/generation_config.json max_new_tokens"
                if generation.get("max_new_tokens") == published_output
                else "model/generation_config.json max_tokens")
        else:
            # A worker default must say it is a worker default: this is not a number the
            # checkpoint stated, and a reader must not inherit it as one. The reason names the
            # exact state of the checkpoint, because "no generation_config.json" and "a
            # generation_config.json with no token bound" are different facts.
            self.max_output_tokens = FALLBACK_MAX_OUTPUT_TOKENS
            self.max_output_tokens_source_kind = "worker_default"
            self.max_output_tokens_source = (
                "worker default: the checkpoint ships no generation_config.json"
                if not generation_path.is_file() else
                "worker default: model/generation_config.json states no max_new_tokens")
        self.label = self._label(config)
        self.family = self._family(config)

    def _manifest_memory_tier(self) -> float | None:
        """The qualified memory tier the manifest publishes, or ``None``.

        The manifest is the model's own metadata, so it is read here rather than in the API
        layer: one derivation, and a worker pointed at a different manifest follows it.
        """
        qualification = self.manifest.get("qualification")
        if not isinstance(qualification, dict):
            return None
        value = qualification.get("minimum_unified_memory_gb")
        if value is None:
            value = qualification.get("recommended_unified_memory_gb")
        if isinstance(value, bool) or value is None:
            return None
        try:
            tier = float(value)
        except (TypeError, ValueError):
            # A malformed tier is not a tier: report none rather than inventing one.
            return None
        return tier if tier > 0 else None

    def _label(self, config: dict) -> str:
        """This model's own label - never a literal in this worker.

        The manifest may name one (a field a later manifest schema can carry); otherwise the
        label is the repo's own name, which already carries the quantization. A second chat
        model can therefore never be published under this worker's first model's name.
        """
        declared = self.manifest.get("label") or self.manifest.get("name")
        if isinstance(declared, str) and declared.strip():
            return declared.strip()
        return str(self.model_id).rsplit("/", 1)[-1] or str(self.model_id)

    def _family(self, config: dict) -> str | None:
        """The model family the checkpoint itself reports, or ``None``.

        ``model_type`` is the checkpoint's own word for its architecture. Nothing here guesses a
        family from a name: the Hub and the app already fall back to the lane's own label when a
        model does not state one.
        """
        declared = self.manifest.get("family")
        if isinstance(declared, str) and declared.strip():
            return declared.strip()
        model_type = config.get("model_type")
        return model_type.strip() if isinstance(model_type, str) and model_type.strip() else None

    def model_limits(self) -> dict:
        """Every limit this model states, each with the source that states it.

        ``value`` is ``None`` when nothing states the limit, and ``source`` is then the reason -
        never a curated substitute. A reader labels its own fallback rather than inheriting this
        worker's number as the model's fact.

        **``source_kind`` is the machine-readable half of the same decision** (HUB-20). ``source``
        is a sentence and a sentence is not something a consumer can branch on — and the consumer
        here is two hops away, with a gateway in between that must relay rather than interpret.
        Without a token the gateway had to guess, and it guessed ``provider`` for every stated
        value: the live row publishes ``max_output_tokens: 128000`` with
        ``source_kind: worker_default`` here, and the gateway was describing that as a number the
        *model* stated. That is the claim this field exists to stop.

        The vocabulary is the consumer's, because it is the one doing the labelling:
        ``model`` (the checkpoint's own file states it) · ``curated`` (this Hub's packaged
        metadata, not the checkpoint's word) · ``worker_default`` (this worker's fallback) ·
        ``unknown`` (nothing states it, and ``value`` is ``None``). ``source`` is still published
        beside it — a sentence is what a person reads, and the pair is one decision made here
        rather than two that can disagree.
        """
        return {
            "context_window_tokens": {
                "value": self.context_window_tokens,
                "source": self.context_window_source,
                "source_kind": self.context_window_source_kind,
            },
            "max_output_tokens": {
                "value": self.max_output_tokens,
                "source": self.max_output_tokens_source,
                "source_kind": self.max_output_tokens_source_kind,
            },
        }

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
        _bound_mlx_cache()
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
            # Balanced for every ending. `started > finished` means a stream is still holding
            # the model, which is a fact an operator can read rather than guess.
            "streams_started": self._streams_started,
            "streams_finished": self._streams_finished,
        }

    # ---- generation ------------------------------------------------------
    def _count_tokens_unlocked(self, text: str) -> int | None:
        """Count tokens with the loaded tokenizer. The caller holds ``self._lock``."""
        tokenizer = self._tokenizer
        if tokenizer is None:
            return None
        try:
            return len(tokenizer.encode(text))
        except Exception:
            return None

    def count_tokens(self, text: str) -> int | None:
        """Tokenizer count for the loaded model, or ``None`` when nothing is loaded."""
        with self._lock:
            return self._count_tokens_unlocked(text)

    def prompt_length(self, messages: list[dict]) -> int | None:
        with self._lock:
            if self._tokenizer is None:
                return None
            return self._count_tokens_unlocked(self._prompt(messages))

    def _budget(self, max_tokens: int | None) -> int:
        return min(int(max_tokens or self.max_output_tokens), self.max_output_tokens)

    def _named_a_figure_of_its_own(self, max_tokens: int | None) -> bool:
        """Whether this request named a figure of its own, rather than one this lane serves with.

        The route resolves an omitted `max_tokens` to the lane's published default
        (`main._lane_output_budget`, which is `min(FALLBACK_MAX_OUTPUT_TOKENS, ceiling)`), so by
        the time the engine sees the value, *"the caller asked for nothing"* and *"the caller
        asked for exactly the lane's default"* are one number and cannot be told apart. The lane
        therefore treats that figure as its own — a request naming something **else** named a
        choice, and that choice is refused when it does not fit the model's context. See
        `_refuse_over_context` for what "ours" changes.
        """
        if max_tokens is None:
            return False
        return int(max_tokens) != min(FALLBACK_MAX_OUTPUT_TOKENS, self.max_output_tokens)

    def _refuse_over_context(self, prompt_tokens: int | None, budget: int, *,
                             asked: bool) -> int:
        """Refuse a prompt that cannot fit the context the model states — and return the
        allowance the request will actually run with.

        Written as a refusal, not a truncation: a caller that sends more than the model can hold
        is told the measured count and the model's own number. Where nothing states a context
        window the check refuses nothing - this worker does not invent a limit.

        **A number this worker filled in is not the caller's, so it is not the caller's to be
        refused for.** `asked=False` means the request named no figure of its own
        (`_named_a_figure_of_its_own`) and `_budget` served the worker default — 128,000 since
        the owner's 2026-10-19 decision ("the minimum is at least 128K tokens"). A model that
        states a smaller context than that would otherwise REFUSE every request that names no
        allowance: gemma4-26b-a4b-qat-4bit states `qualification.context_window_tokens = 32768`,
        and the prompt alone would never leave 128,000 of room, so the model would be unusable
        for exactly the caller that asked for nothing. So our own default shrinks to the room the
        context leaves and the caller gets the longest answer the model can hold. A figure the
        request named itself is never shrunk — it is theirs to be refused, with the measured
        counts, exactly as before.
        """
        limit = self.context_window_tokens
        if limit is None or prompt_tokens is None:
            return budget
        if prompt_tokens + budget <= limit:
            return budget
        if not asked:
            room = limit - prompt_tokens
            if room >= 1:
                return room
            raise ChatContextExceeded(
                f"messages: the prompt is {prompt_tokens} tokens, above the {limit} tokens this "
                f"model states ({self.context_window_source}). Send a shorter prompt; this worker "
                f"refuses rather than truncating.")
        raise ChatContextExceeded(
            f"messages: the prompt is {prompt_tokens} tokens and max_tokens asks for "
            f"{budget} more, {prompt_tokens + budget} together, above the {limit} tokens "
            f"this model states ({self.context_window_source}). Send a shorter prompt or a "
            f"smaller max_tokens; this worker refuses rather than truncating.")

    def check_prompt_fits(self, messages: list[dict], *, max_tokens: int | None = None,
                          response_format=None, thinking: bool = False) -> int | None:
        """Measure a request against the model's stated context before anything is streamed.

        A streaming response has already committed its status line by the time the engine
        produces its first token, so the refusal has to happen here, before the SSE response
        starts. The non-streaming path is refused inside ``complete()`` by the same check.
        """
        with self._lock:
            self._load_locked()
            working = self._with_instruction(messages, self.json_instruction(response_format))
            budget = self._budget(max_tokens)
            prompt_tokens = self._count_tokens_unlocked(self._prompt(working, thinking=thinking))
            # Nothing is generated here, so the allowance this returns is not kept: the check
            # only has to agree with the generation path that this request would be accepted.
            asked = self._named_a_figure_of_its_own(max_tokens)
            self._refuse_over_context(prompt_tokens, budget, asked=asked)
            return prompt_tokens

    def _prompt(self, messages: list[dict], *, thinking: bool = False) -> str:
        tokenizer = self._tokenizer
        apply = getattr(tokenizer, "apply_chat_template", None)
        messages = self._with_instruction(messages, None)
        if callable(apply):
            try:
                try:
                    # Qwen3.5-9B emits a long "Thinking Process:" trace before the answer when the
                    # template's default is left on, which also breaks JSON mode. Thinking is off
                    # by default and can be requested per call.
                    return apply(messages, tokenize=False, add_generation_prompt=True,
                                 enable_thinking=thinking)
                except TypeError:
                    # A tokenizer whose chat template takes no `enable_thinking` kwarg.
                    return apply(messages, tokenize=False, add_generation_prompt=True)
            except Exception as exc:
                # jinja2.TemplateError, matched by name so this module needs no jinja2 import.
                if any(cls.__name__ == "TemplateError" for cls in type(exc).__mro__):
                    raise ChatTemplateError(
                        f"messages: this model's chat template refused the conversation: {exc}"
                    ) from exc
                raise
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
        """The conversation with ``instruction`` folded into one leading system message.

        The Qwen3.5 and Gemma chat templates accept a system message only first (and only one), so
        the engine never *adds* a second: a caller's system text and the JSON contract share one
        message. Several or late system messages from a caller are gathered into that same leading
        message in their original order, the other turns keep theirs, and a conversation that
        already has one leading system message and no instruction comes back untouched.
        """
        system = [m for m in messages if m.get("role") == "system"]
        if not instruction and (not system or (len(system) == 1 and messages[0] is system[0])):
            return messages
        text = [str(m.get("content") or "").strip() for m in system]
        merged = "\n\n".join(part for part in [*text, instruction or ""] if part)
        rest = [m for m in messages if m.get("role") != "system"]
        return [{"role": "system", "content": merged}, *rest] if merged else rest

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

    @staticmethod
    def _emit_progress(on_progress: Callable[[dict], None] | None, phase: str,
                       attempt: int, **fields) -> None:
        if on_progress is not None:
            on_progress({"phase": phase, "attempt": attempt, **fields})

    @staticmethod
    def _response_metrics(response) -> dict:
        """Copy only finite metrics stated by one mlx-lm GenerationResponse."""
        metrics: dict = {}
        for name in ("prompt_tokens", "generation_tokens"):
            value = getattr(response, name, None)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                metrics[name] = value
        for name in ("prompt_tps", "generation_tps"):
            value = getattr(response, name, None)
            if (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) and value >= 0):
                metrics[name] = float(value)
        # mlx-lm states peak_memory in decimal GB (mx.get_peak_memory() / 1e9).
        peak_gb = getattr(response, "peak_memory", None)
        if (isinstance(peak_gb, (int, float)) and not isinstance(peak_gb, bool)
                and math.isfinite(peak_gb) and peak_gb >= 0):
            metrics["peak_memory_bytes"] = round(peak_gb * 1_000_000_000)
        finish_reason = getattr(response, "finish_reason", None)
        if isinstance(finish_reason, str) and finish_reason:
            metrics["finish_reason"] = finish_reason
        return metrics

    def _prompt_progress(self, on_progress: Callable[[dict], None] | None,
                         attempt: int) -> Callable[[int, int], None]:
        def report(processed, total) -> None:
            fields = {}
            if (isinstance(processed, int) and not isinstance(processed, bool)
                    and processed >= 0 and isinstance(total, int)
                    and not isinstance(total, bool) and total >= 0):
                fields["processed_prompt_tokens"] = processed
                fields["total_prompt_tokens"] = total
            self._emit_progress(on_progress, "processing", attempt, **fields)
        return report

    def _usage_unlocked(self, prompt_tokens: int | None, text: str) -> dict:
        prompt = (prompt_tokens if isinstance(prompt_tokens, int)
                  and not isinstance(prompt_tokens, bool) and prompt_tokens >= 0 else None)
        counted = self._count_tokens_unlocked(text)
        completion = (counted if isinstance(counted, int)
                      and not isinstance(counted, bool) and counted >= 0 else None)
        total = prompt + completion if prompt is not None and completion is not None else None
        return {"prompt_tokens": prompt, "completion_tokens": completion,
                "total_tokens": total}

    def complete(self, messages: list[dict], *, max_tokens: int | None = None,
                 temperature: float = DEFAULT_TEMPERATURE, top_p: float = DEFAULT_TOP_P,
                 response_format=None, seed=None, thinking: bool = False,
                 on_progress: Callable[[dict], None] | None = None) -> dict:
        self._emit_progress(on_progress, "waiting", 1)
        with self._lock:
            if self._model is None:
                self._emit_progress(on_progress, "loading", 1)
            model, tokenizer = self._load_locked()
            budget = self._budget(max_tokens)
            instruction = self.json_instruction(response_format)
            working = self._with_instruction(messages, instruction)
            prompt_tokens = self._count_tokens_unlocked(self._prompt(working, thinking=thinking))
            budget = self._refuse_over_context(prompt_tokens, budget, asked=self._named_a_figure_of_its_own(max_tokens))
            self._generating = True
            started = time.time()
            try:
                from mlx_lm import stream_generate
                from mlx_lm.sample_utils import make_sampler

                # mlx-lm 0.31 asks for a sampler, not raw `temp`/`top_p`: its
                # `generate_step` has no such parameters and would raise TypeError.
                sampler = make_sampler(temp=temperature, top_p=top_p)
                self._apply_seed(seed)
                attempts: list[dict] = []

                def generate(prompt: str, attempt: int) -> str:
                    text_parts: list[str] = []
                    attempt_metrics = {"attempt": attempt}
                    for response in stream_generate(
                            model, tokenizer, prompt=prompt, max_tokens=budget,
                            sampler=sampler,
                            prompt_progress_callback=self._prompt_progress(
                                on_progress, attempt)):
                        text_parts.append(getattr(response, "text", ""))
                        metrics = self._response_metrics(response)
                        attempt_metrics.update(metrics)
                        self._emit_progress(on_progress, "generating", attempt, **metrics)
                    attempts.append(attempt_metrics)
                    return "".join(text_parts)

                text = generate(self._prompt(working, thinking=thinking), 1)
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
                        text = generate(self._prompt(correction, thinking=thinking), 2)
                        json_valid = self._valid_json(text)
            finally:
                self._generating = False
                self._last_activity_at = time.time()
            telemetry = {"attempts": attempts, "final_attempt": len(attempts)}
            usage = self._usage_unlocked(prompt_tokens, text)
            if instruction and not json_valid:
                raise ChatStructuredOutputError(
                    "structured output: the model did not return valid JSON after one repair")
            final_metrics = {key: value for key, value in attempts[-1].items()
                             if key != "attempt"}
            self._emit_progress(on_progress, "completed", len(attempts), **final_metrics,
                                telemetry=telemetry, usage=usage)
            return {
                "text": text,
                "elapsed_seconds": round(time.time() - started, 3),
                "max_tokens": budget,
                "prompt_tokens": prompt_tokens,
                "json_valid": json_valid,
                "repairs": repairs,
                "telemetry": telemetry,
                "usage": usage,
            }

    def stream(self, messages: list[dict], *, max_tokens: int | None = None,
               temperature: float = DEFAULT_TEMPERATURE, top_p: float = DEFAULT_TOP_P,
               response_format=None, seed=None, thinking: bool = False,
               on_progress: Callable[[dict], None] | None = None,
               ) -> Generator[str, None, float]:
        """Stream one completion's text, returning the generation window it measured.

        The generator's **return value** is ``elapsed_seconds`` for this generation: the time
        spent producing it, started in exactly the place ``complete()`` starts its own (after the
        model is resident), so "how long the engine spent generating" means the same thing on
        both paths and anything derived from either is comparable. A cold turn's model load is
        therefore not in it.

        It is returned with the stream rather than stored on the engine because it belongs to one
        request: an attribute would be shared state, and the next request could overwrite it
        between this stream ending and its caller writing the final chunk - publishing another
        request's number as this one's.
        """
        # The lock is taken and released explicitly rather than with `with`, because this is a
        # generator: its consumer may abandon it (a client that disconnects mid-SSE), and an
        # abandoned generator is finalised at an arbitrary later moment, on whatever thread drops
        # the last reference. `with` would release only on finalisation, and it would release
        # from the finalising thread, which a `threading.RLock` refuses. One explicit release,
        # owned by this call and performed at most once, is what returns the model to the next
        # request however this stream ends - completed, errored, cancelled, or client gone.
        self._emit_progress(on_progress, "waiting", 1)
        self._lock.acquire()
        released = False

        def release_lock() -> None:
            nonlocal released
            if released:
                return
            released = True
            self._lock.release()

        self._streams_started += 1
        try:
            if self._model is None:
                self._emit_progress(on_progress, "loading", 1)
            model, tokenizer = self._load_locked()
            budget = self._budget(max_tokens)
            working = self._with_instruction(messages, self.json_instruction(response_format))
            prompt_tokens = self._count_tokens_unlocked(self._prompt(working, thinking=thinking))
            budget = self._refuse_over_context(prompt_tokens, budget, asked=self._named_a_figure_of_its_own(max_tokens))
            self._generating = True
            self._last_activity_at = time.time()
            # Where `complete()` starts its own window: after `_load_locked()`, so a cold turn's
            # model load is not counted as generation.
            generation_started = time.time()
            try:
                from mlx_lm import stream_generate
                from mlx_lm.sample_utils import make_sampler

                sampler = make_sampler(temp=temperature, top_p=top_p)
                self._apply_seed(seed)
                attempt_metrics = {"attempt": 1}
                text_parts: list[str] = []
                for response in stream_generate(model, tokenizer, prompt=self._prompt(working, thinking=thinking),
                                                max_tokens=budget, sampler=sampler,
                                                prompt_progress_callback=self._prompt_progress(
                                                    on_progress, 1)):
                    self._last_activity_at = time.time()
                    metrics = self._response_metrics(response)
                    attempt_metrics.update(metrics)
                    self._emit_progress(on_progress, "generating", 1, **metrics)
                    piece = getattr(response, "text", "")
                    text_parts.append(piece)
                    yield piece
            finally:
                self._generating = False
                self._last_activity_at = time.time()
            telemetry = {"attempts": [attempt_metrics], "final_attempt": 1}
            final_metrics = {key: value for key, value in attempt_metrics.items()
                             if key != "attempt"}
            usage = self._usage_unlocked(prompt_tokens, "".join(text_parts))
            self._emit_progress(on_progress, "completed", 1, **final_metrics,
                                telemetry=telemetry, usage=usage)
            return round(max(0.0, time.time() - generation_started), 3)
        finally:
            # Count the ending before the lock is free, so a reader that sees the lock released
            # also sees the counters balanced.
            self._streams_finished += 1
            release_lock()


def engine_from_environment() -> ChatEngine:
    """Construct the engine from the environment the packaged worker exports."""
    models_dir = os.environ.get("STUDIOHUB_MODELS_DIR")
    manifest_path = os.environ.get("STUDIOHUB_CHAT_MANIFEST")
    if not models_dir or not manifest_path:
        raise ChatModelUnavailable("STUDIOHUB_MODELS_DIR and STUDIOHUB_CHAT_MANIFEST are required")
    path = Path(manifest_path)
    manifest = json.loads(path.read_text())
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
        manifest=manifest,
        manifest_path=path,
    )
