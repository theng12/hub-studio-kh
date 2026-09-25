"""Private standalone Chat API for one pinned local MLX text model.

Serves the OpenAI-compatible surface the Hub and (later) gateway-kh can call, plus the
readiness, catalog and memory-policy contracts every Studio in this app publishes. It
deliberately has no job queue of its own: a completion is a single request/response, and
the Hub owns admission, dispatch and history.
"""
from __future__ import annotations

import hmac
import json
import math
import os
import platform
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.datastructures import Headers

from components import app_version

from . import memory_policy as memory_policy_module
from .llm_engine import (DEFAULT_TEMPERATURE, DEFAULT_TOP_P, ChatBusy, ChatContextExceeded,
                         ChatEngine, ChatModelUnavailable, engine_from_environment)

# One stable creation stamp for the OpenAI model listing.
_MODELS_CREATED = int(time.time())

# The Hub's live-activity contract. The worker owns its own job state; this publishes it in the
# allowlisted shape the Hub's `activity.validate_snapshot` accepts, nothing more.
ACTIVITY_SCHEMA = "kh-studio.activity.v1"
ACTIVITY_STUDIO = "chat"
# The Hub names a dispatched pack with its own id so the live view has one identity per job. A
# direct caller's own id is accepted under the same bound the Hub enforces (160 characters).
_JOB_ID = re.compile(r"[A-Za-z0-9._:-]{1,160}")
# The lane contract's schema name.
LANE_CONTRACT_SCHEMA = "studiohub.lane-contract.v1"

# Every top-level field this route consumes, and therefore everything a request may carry. A
# field outside this set is refused by name: a parameter accepted and then discarded is worse
# than one refused, because the caller believes it took effect. `job_id` is the Hub's own
# correlation id; `max_completion_tokens` is the one alias the lane honours for `max_tokens`.
SUPPORTED_FIELDS = frozenset({
    "model", "messages", "stream", "job_id",
    "max_tokens", "max_completion_tokens",
    "temperature", "top_p", "response_format", "seed", "thinking",
})


def _describe(value: object) -> str:
    """What was received, in the caller's own terms: its type and its value."""
    return f"{type(value).__name__} {value!r}"


class _StreamDone:
    """Sentinel for the end of a stream: `StopIteration` cannot cross a thread boundary.

    ``stated`` carries the generator's own return value across that boundary - for the Chat
    engine, the generation window it measured (``ChatEngine.stream``). It is ``None`` for a
    stream that ended without stating one, so the route publishes no number rather than a
    default.
    """

    def __init__(self, stated: float | None = None) -> None:
        self.stated = stated


def _next_or_done(iterator):
    """One step of a sync generator, run in a worker thread by the streaming route."""
    try:
        return next(iterator)
    except StopIteration as stop:
        return _StreamDone(stop.value)


class _OwnerCredentialMiddleware:
    """The owner-credential gate, as a pass-through ASGI middleware.

    Deliberately **not** ``BaseHTTPMiddleware``: that relays the response through an anyio
    memory stream and does not deliver the client-disconnect cancellation to the route body,
    which is how an abandoned SSE request kept the model lock held until the process exited
    (measured: the engine's generator was closed only at loop shutdown). This middleware reads
    headers, answers 401 itself before the body is parsed or any route is matched, and
    otherwise forwards the same ``receive``/``send`` untouched, so the streaming response path
    stays direct and a disconnect reaches the generator that holds the model.
    """

    def __init__(self, app, *, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        offered = (headers.get("x-studio-token") or headers.get("x-hub-token")
                   or (headers.get("authorization") or "").removeprefix("Bearer "))
        if not hmac.compare_digest(offered, self.token):
            response = JSONResponse({"detail": "Owner credential required"}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _model_label(engine) -> str:
    """The model's own label, tolerant of a stub engine in tests."""
    label = getattr(engine, "label", None)
    if isinstance(label, str) and label.strip():
        return label.strip()
    return str(engine.model_id)


def _published_max_output_tokens(engine) -> tuple[int | None, str | None]:
    """The output ceiling the lane publishes, with the source that states it."""
    limits = engine.model_limits() if hasattr(engine, "model_limits") else {}
    entry = limits.get("max_output_tokens") if isinstance(limits, dict) else None
    entry = entry if isinstance(entry, dict) else {}
    value = entry.get("value")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None, entry.get("source")
    return value, entry.get("source")


def _usage(engine: ChatEngine, prompt_tokens: int | None, text: str) -> dict:
    completion = engine.count_tokens(text)
    total = None
    if prompt_tokens is not None and completion is not None:
        total = prompt_tokens + completion
    return {"prompt_tokens": prompt_tokens, "completion_tokens": completion,
            "total_tokens": total}


def _qualified_minimum_memory_bytes(engine) -> int | None:
    """The memory tier the model manifest publishes, or ``None`` when it publishes none."""
    value = getattr(engine, "minimum_unified_memory_gb", None)
    if isinstance(value, bool):
        return None
    try:
        gb = float(value)
    except (TypeError, ValueError):
        return None
    return int(gb * 1e9) if gb > 0 else None


def _host_eligibility(engine) -> dict:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return {"eligible": False, "reason": "Apple Silicon macOS is required"}
    try:
        memory = int(subprocess.check_output(["/usr/sbin/sysctl", "-n", "hw.memsize"],
                                             text=True, timeout=3).strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"eligible": False, "reason": "Unified memory could not be measured"}
    version = platform.mac_ver()[0]
    if not version:
        return {"eligible": False, "reason": "macOS version could not be measured"}
    if int(version.split(".")[0]) < 14:
        return {"eligible": False, "reason": "macOS 14 or later is required"}
    qualified_bytes = _qualified_minimum_memory_bytes(engine)
    label = _model_label(engine)
    state = {"eligible": True, "macos": version, "unified_memory_bytes": memory,
             "qualified_minimum_memory_bytes": qualified_bytes,
             "memory_warning": None,
             "qualification": (
                 f"{qualified_bytes / 1e9:g} GB is the tier this model's own manifest "
                 f"qualifies it at; this host still needs packaged proof"
                 if qualified_bytes else
                 "the model manifest publishes no qualified memory tier")}
    if qualified_bytes and memory < qualified_bytes:
        state["memory_warning"] = (
            f"This Mac has {memory / 1024**3:.0f} GB of unified memory. {label} is qualified at "
            f"{qualified_bytes / 1e9:g} GB; on a smaller Mac it will swap or fail, especially "
            "with another Studio holding a model.")
    return state


class ChatActivity:
    """This worker's own job state, in the Hub's allowlisted activity contract.

    A completion is a single request/response, so this worker has no queue: the jobs it knows
    about are the requests it is serving right now and the last one that finished. Published
    honestly - an idle worker reports both slots as null rather than inventing a job, and an
    abandoned stream is ``cancelled`` rather than ``done``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict | None = None
        self._latest: dict | None = None

    def begin(self, *, job_id: str, model: str) -> None:
        now = time.time()
        with self._lock:
            self._active = {
                "id": job_id, "state": "running", "model": model,
                "source": "direct", "origin": "unknown",
                "created_at": now, "started_at": now, "updated_at": now,
            }

    def finish(self, *, state: str, error_code: str | None = None) -> None:
        now = time.time()
        with self._lock:
            active = self._active
            self._active = None
            if active is None:
                return
            started = float(active.get("started_at") or now)
            latest = {
                **active, "state": state, "updated_at": now, "finished_at": now,
                "runtime_s": round(max(0.0, now - started), 3),
            }
            latest.pop("progress", None)
            if error_code:
                latest["error_code"] = error_code
            self._latest = latest

    def snapshot(self, engine) -> dict:
        with self._lock:
            active = dict(self._active) if self._active else None
            latest = dict(self._latest) if self._latest else None
        return {
            "schema": ACTIVITY_SCHEMA, "studio": ACTIVITY_STUDIO,
            "observed_at": time.time(), "active": active, "latest": latest,
            "loaded": bool(engine.loaded()),
        }


def _parameter_surface(engine) -> list[dict]:
    """The parameters this lane actually honours, with the bounds it enforces.

    Built from the model's own limits and this worker's defaults - not from a literal - so the
    contract cannot go stale against the code. A parameter this worker refuses is deliberately
    absent, and a bound published here is the bound the route enforces: the two are asserted
    equal by ``tests/test_chat_worker.py``.
    """
    limits = engine.model_limits() if hasattr(engine, "model_limits") else {}
    limits = limits if isinstance(limits, dict) else {}
    output_limit, output_source = _published_max_output_tokens(engine)
    context = limits.get("context_window_tokens") or {}
    context_value = context.get("value")
    context_source = context.get("source")

    prompt_note = "OpenAI-shaped messages, each with a string content field"
    if isinstance(context_value, int) and not isinstance(context_value, bool) and context_value > 0:
        prompt_note += (
            f"; the rendered prompt is measured and the request is refused by name when prompt "
            f"tokens + max_tokens exceed the {context_value} tokens this model states "
            f"({context_source})")
    else:
        prompt_note += (
            "; nothing states a context window for this model, so no prompt length is refused")

    parameters = [
        {"name": "model", "type": "string", "required": False, "default": engine.model_id,
         "note": "the one model this worker serves; any other value is refused by name"},
        {"name": "messages", "type": "array", "required": True, "note": prompt_note},
        {"name": "max_tokens", "type": "integer", "required": False, "minimum": 1,
         "note": ("max_completion_tokens is accepted as an alias; a value above the published "
                  "maximum is refused by name")},
        {"name": "temperature", "type": "number", "required": False,
         "default": DEFAULT_TEMPERATURE, "minimum": 0.0,
         "note": ("no maximum is published: neither the model nor this worker states one. A "
                  "non-finite value (NaN, Infinity) is refused by name")},
        {"name": "top_p", "type": "number", "required": False, "default": DEFAULT_TOP_P,
         "minimum": 0.0, "minimum_exclusive": True, "maximum": 1.0},
        {"name": "stream", "type": "boolean", "required": False, "default": False,
         "note": ("true answers text/event-stream; the Hub's dispatch path asks for one JSON "
                  "completion per pack")},
        {"name": "response_format", "type": "object", "required": False,
         "note": "type must be json_object or json_schema; anything else is refused"},
        {"name": "seed", "type": "integer", "required": False,
         "note": "a real integer; a non-integer is refused rather than silently dropped"},
        {"name": "thinking", "type": "boolean", "required": False, "default": False,
         "note": ("must be a real JSON boolean; a string such as \"false\" is refused rather "
                  "than coerced, because bool(\"false\") is true")},
    ]
    if output_limit is not None:
        entry = next(item for item in parameters if item["name"] == "max_tokens")
        entry["maximum"] = output_limit
        entry["default"] = output_limit
        entry["note"] += f". This model's ceiling is {output_limit} ({output_source})"
    return parameters


def _lane_contract(engine) -> dict:
    """This lane's dispatch contract: what to send, what comes back, what a cancel promises.

    `runtime_seconds` is deliberately absent: nothing has measured what one pack of this lane
    costs on this fleet, and a guessed runtime is how a scheduler sets a deadline that fails in
    use. `deadline_seconds` is deliberately absent too - it is the Hub's own peer-proxy timeout,
    added by the Hub when it echoes this contract, because a worker restating a Hub number is
    the duplication this contract exists to remove.
    """
    return {
        "schema": LANE_CONTRACT_SCHEMA,
        "modality": "chat",
        "operation": "chat.completion",
        "parameters": _parameter_surface(engine),
        "artifact": {
            "modality": "chat",
            "media_type": "application/json",
            "extension": "json",
            # One completion response per pack.
            "per_item": 1,
        },
        "states": {
            "accepted": ["queued"],
            "active": ["running", "cancel_requested"],
            "terminal": ["done", "error", "cancelled"],
            "uncertain": "uncertain",
            # A Hub-side item whose outcome cannot be established is held for an operator; it is
            # never finished. Chat's own module today has no uncertain state, but the Hub's
            # vocabulary is the contract and a reader must not treat it as terminal.
            "uncertain_is_terminal": False,
            # A pack produces a completion, not a file: there is nothing to discard.
            "discard_supported": False,
        },
        "cancel": {
            "supported": True,
            "request": "DELETE /api/hub/jobs/{batch_id}",
            "semantics": (
                "stops dispatching further packs of the batch; a pack already in flight is a "
                "single synchronous completion this worker has no cancel endpoint for, so it "
                "runs to completion and a cancelled pack is not evidence that no completion was "
                "produced"),
        },
    }


def create_app(engine: ChatEngine, policy: memory_policy_module.MemoryPolicy,
               token: str, runtime_sha256: str) -> FastAPI:
    if not token:
        raise ValueError("Private Chat worker requires its owning Hub credential")
    app = FastAPI(title="Studio Hub Chat", version="1")
    app.add_middleware(_OwnerCredentialMiddleware, token=token)
    activity = ChatActivity()

    def readiness() -> dict:
        files = engine.preflight()
        host = _host_eligibility(engine)
        return {"ready": files["state"] == "files_verified" and host["eligible"],
                "files": files, "host": host,
                "runtime_identity": {"id": runtime_sha256, "compatible": True}}

    @app.get("/api/health")
    def health() -> dict:
        state = engine.status()
        return {"ok": True, **app_version.version_fields(), "generation": {
            "available": True, "busy": state["busy"], "queued": 0,
            "running": 1 if state["busy"] else 0, "runtime_revision": runtime_sha256}}

    @app.get("/api/fleet/activity")
    def fleet_activity() -> dict:
        """This worker's own job state, in the Hub's allowlisted activity contract.

        Chat has no queue, so an idle worker reports an empty snapshot rather than a fabricated
        job. While a completion is running, the request it is serving is the active item; the
        last completed request is the latest one. Before this, the route answered
        ``{"active": None, "latest": None}`` unconditionally, so Chat work appeared in no
        activity view anywhere even after the Hub learned to read the chat queue.
        """
        return activity.snapshot(engine)

    @app.get("/api/catalog")
    def catalog() -> dict:
        state = readiness()
        ready = state["ready"]
        files = state["files"]
        qualified_bytes = _qualified_minimum_memory_bytes(engine)
        qualified_gb = qualified_bytes / 1e9 if qualified_bytes else None
        return {"models": [{
            # The model's own label, read from its manifest/repo id - never a literal here, so
            # a second chat model cannot be published under the first one's name. It carries the
            # quantization, which is what a reader needs to check it against what was measured.
            "label": _model_label(engine), "name": _model_label(engine),
            "repo": engine.model_id, "model_id": engine.model_id,
            "family": getattr(engine, "family", None), "modality": "chat",
            "capabilities": ["chat.completion"], "hub_operation": "chat.completion",
            "model_revision": engine.revision, "qualified_revision": engine.revision,
            "qualified_revision_match": True, "runtime_revision": runtime_sha256,
            "runtime_compatible": True, "runtime_ready": True,
            "verified_token_usage": False,
            "memory_eligible": state["host"]["eligible"],
            # Both spellings the Hub's governor reads, from the model's own manifest. `None`
            # where the manifest publishes no tier, rather than a number this worker invented.
            "min_total_memory_gb": qualified_gb,
            "min_unified_memory_gb": qualified_gb,
            "qualified_minimum_memory_gb": qualified_gb,
            "memory_warning": state["host"].get("memory_warning"),
            "cache": {"state": "cached" if files["state"] == "files_verified" else files["state"]},
            "readiness": {"ready": ready, "files": files, "host": state["host"],
                          "reason": None if ready else files.get("reason") or state["host"].get("reason")},
            "execution_ready": ready, "available": ready,
            "context_window_tokens": engine.context_window_tokens,
            "max_output_tokens": engine.max_output_tokens,
            # Every limit with the source that states it, so a reader can label its own fallback
            # instead of inheriting this worker's number as the model's fact.
            "model_limits": engine.model_limits() if hasattr(engine, "model_limits") else None,
            # The defaults the route actually serves with, from the same constants the route
            # and the lane contract use - not a second copy free to disagree with them.
            "generation_defaults": {"temperature": DEFAULT_TEMPERATURE, "top_p": DEFAULT_TOP_P},
            # The dispatch contract, published by the only party that knows this API. The Hub
            # passes it through verbatim and adds its own `deadline_seconds`.
            "lane_contract": _lane_contract(engine),
            "qualification": {
                "owner_decision": f"{_model_label(engine)}, MLX, text-only",
                # This string used to read "pending measured proof" long after the proof existed.
                # It now names the evidence and its dates instead of a byte count this worker
                # cannot re-measure at publish time.
                "packaged_inference": (
                    "proven: the packaged worker loaded this 4-bit snapshot and produced real "
                    "completions, measured 2026-09-24 and re-checked 2026-09-25 "
                    "(qualification/st14-chat-2026-09-24.md)"),
            },
        }]}

    def _job_identifier(body: dict) -> str:
        """The id this request is tracked under: the Hub's, when it named one."""
        requested = body.get("job_id")
        if isinstance(requested, str) and _JOB_ID.fullmatch(requested.strip()):
            return requested.strip()
        return "chatcmpl-" + uuid.uuid4().hex

    def _body_model(body: dict) -> None:
        requested = body.get("model")
        if requested and requested != engine.model_id:
            directory = (str(getattr(engine, "manifest_path", None).parent)
                         if getattr(engine, "manifest_path", None) else
                         "the shipped model-manifests directory")
            raise HTTPException(404, (
                f"this worker serves only {engine.model_id}. It is a single-model worker: to "
                f"serve another model, add its manifest to {directory} and start this worker "
                f"with STUDIOHUB_CHAT_MANIFEST pointing at that file."))

    def _refuse_unsupported_fields(body: dict) -> None:
        """Refuse, by name, every field this worker does not honour.

        An OpenAI client that sends `tools`, `n` or `logit_bias` here must learn that this lane
        serves one synchronous completion and has none of them. Accepting the request and
        dropping the parameter is the lie the caller cannot see.
        """
        unsupported = sorted(set(body) - SUPPORTED_FIELDS)
        if not unsupported:
            return
        named = "; ".join(f"{name} = {_describe(body[name])}" for name in unsupported)
        raise HTTPException(422, (
            f"{', '.join(unsupported)}: not supported by this worker (received {named}). This "
            f"lane accepts {', '.join(sorted(SUPPORTED_FIELDS))}; a parameter it does not honour "
            f"is refused rather than silently dropped."))

    def _number(body: dict, name: str, default: float) -> float:
        value = body.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HTTPException(422, f"{name}: must be a number; received {_describe(value)}")
        number = float(value)
        if not math.isfinite(number):
            raise HTTPException(422, (
                f"{name}: must be a finite number; received {_describe(value)}, which reaches the "
                f"sampler as {number!r} and produces meaningless output"))
        return number

    def _completion_parameters(body: dict) -> dict:
        """Validate the asked-for surface, refusing rather than silently dropping a value."""
        _refuse_unsupported_fields(body)
        max_tokens = body.get("max_tokens")
        if max_tokens is None:
            max_tokens = body.get("max_completion_tokens")
        if max_tokens is not None:
            if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
                raise HTTPException(
                    422, f"max_tokens: must be an integer of at least 1; received "
                         f"{_describe(max_tokens)}")
            limit, source = _published_max_output_tokens(engine)
            if limit is not None and max_tokens > limit:
                raise HTTPException(422, (
                    f"max_tokens: {max_tokens} is above the maximum this lane publishes "
                    f"({limit}, {source}). Send at most {limit}."))
        temperature = _number(body, "temperature", DEFAULT_TEMPERATURE)
        if temperature < 0:
            raise HTTPException(
                422, f"temperature: must not be negative; received {temperature!r}")
        top_p = _number(body, "top_p", DEFAULT_TOP_P)
        if not 0.0 < top_p <= 1.0:
            raise HTTPException(
                422, f"top_p: must be greater than 0 and at most 1; received {top_p!r}")
        response_format = body.get("response_format")
        if response_format is not None:
            if not isinstance(response_format, dict):
                raise HTTPException(
                    422, f"response_format: must be an object; received "
                         f"{_describe(response_format)}")
            kind = str(response_format.get("type") or "")
            if kind not in {"json_object", "json_schema"}:
                raise HTTPException(
                    422, f"response_format.type: must be json_object or json_schema; received "
                         f"{_describe(response_format.get('type'))}")
        seed = body.get("seed")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise HTTPException(
                422, f"seed: must be an integer or omitted; received {_describe(seed)}")
        thinking = body.get("thinking", False)
        if not isinstance(thinking, bool):
            # `bool("false")` is True in Python, so the old coercion turned thinking ON for a
            # caller that asked for it off. Only a real JSON boolean is accepted.
            raise HTTPException(422, (
                f"thinking: must be a JSON boolean (true or false); received "
                f"{_describe(thinking)}. A string is refused rather than coerced, because "
                f"bool(\"false\") is true and would turn thinking ON."))
        return {"max_tokens": max_tokens, "temperature": temperature, "top_p": top_p,
                "response_format": response_format, "seed": seed, "thinking": thinking}

    def _stream_requested(body: dict) -> bool:
        """`stream` is a real JSON boolean too, for the same reason `thinking` is.

        A truthy string such as ``"false"`` would otherwise start an SSE response for a caller
        that asked for one JSON completion.
        """
        value = body.get("stream", False)
        if not isinstance(value, bool):
            raise HTTPException(422, (
                f"stream: must be a JSON boolean (true or false); received {_describe(value)}. "
                f"A non-boolean is refused rather than coerced."))
        return value

    @app.post("/v1/chat/completions")
    def chat_completions(body: dict):
        state = readiness()
        if not state["host"]["eligible"]:
            raise HTTPException(409, state["host"]["reason"])
        if not state["ready"]:
            raise HTTPException(
                409, f"{_model_label(engine)} is not installed in the selected model library")
        _body_model(body)
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise HTTPException(422, "messages must be a non-empty list")
        for message in messages:
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                raise HTTPException(422, "every message needs string content")
        parameters = _completion_parameters(body)
        stream = _stream_requested(body)
        completion_id = _job_identifier(body)
        created = int(time.time())

        if not stream:
            activity.begin(job_id=completion_id, model=engine.model_id)
            try:
                outcome = engine.complete(messages, **parameters)
            except ChatModelUnavailable as exc:
                activity.finish(state="error", error_code="model_unavailable")
                raise HTTPException(409, str(exc)) from exc
            except ChatBusy as exc:
                activity.finish(state="error", error_code="worker_busy")
                raise HTTPException(409, str(exc)) from exc
            except ChatContextExceeded as exc:
                activity.finish(state="error", error_code="context_exceeded")
                raise HTTPException(422, str(exc)) from exc
            except Exception:
                activity.finish(state="error", error_code="generation_failed")
                raise
            activity.finish(state="done")
            prompt_tokens = outcome.get("prompt_tokens")
            if prompt_tokens is None:
                prompt_tokens = engine.prompt_length(messages)
            return {"id": completion_id, "object": "chat.completion", "created": created,
                    "model": engine.model_id,
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": outcome["text"]}}],
                    "usage": _usage(engine, prompt_tokens, outcome["text"]),
                    "studio_hub": {"elapsed_seconds": outcome["elapsed_seconds"],
                                   "json_valid": outcome.get("json_valid"),
                                   "repairs": outcome.get("repairs")}}

        activity.begin(job_id=completion_id, model=engine.model_id)

        # A streaming response has already committed its 200 by the time the engine produces a
        # token, so a prompt that cannot fit the model's stated context is refused here, before
        # the SSE response starts.
        check_prompt_fits = getattr(engine, "check_prompt_fits", None)
        if callable(check_prompt_fits):
            try:
                check_prompt_fits(messages, max_tokens=parameters["max_tokens"],
                                  response_format=parameters["response_format"],
                                  thinking=parameters["thinking"])
            except ChatContextExceeded as exc:
                activity.finish(state="error", error_code="context_exceeded")
                raise HTTPException(422, str(exc)) from exc
            except ChatModelUnavailable as exc:
                activity.finish(state="error", error_code="model_unavailable")
                raise HTTPException(409, str(exc)) from exc
            except ChatBusy as exc:
                activity.finish(state="error", error_code="worker_busy")
                raise HTTPException(409, str(exc)) from exc

        async def events():
            collected: list[str] = []
            # The terminal state this request reports, unless a branch below changes it. The
            # `finally` is what handles a client that disconnects mid-stream: the generation was
            # never delivered, so it is cancelled rather than claimed as a completion.
            final_state, final_error = "cancelled", "client_disconnected"
            # The generation window the engine measured, stated by the stream itself when it
            # ends - measured in the engine, never around this route, or it would include the
            # model load and mean something different from the buffered path's. `None` when the
            # engine stated none, and then no `studio_hub` block is published at all.
            generation_seconds: float | None = None
            # This generator is async on purpose. Starlette never closes a route generator
            # itself, so a *sync* generator abandoned by a disconnecting client stays suspended
            # holding the engine's model lock for the life of the process - every later request
            # then blocks. Driving the engine in a worker thread keeps the task-group
            # cancellation Starlette already performs on `http.disconnect` inside this body,
            # where the `finally` closes the engine's generator and releases the model.
            stream = engine.stream(messages, **parameters)
            try:
                while True:
                    outcome = await run_in_threadpool(_next_or_done, stream)
                    if isinstance(outcome, _StreamDone):
                        generation_seconds = outcome.stated
                        break
                    delta = outcome
                    if not delta:
                        continue
                    collected.append(delta)
                    chunk = {"id": completion_id, "object": "chat.completion.chunk",
                             "created": created, "model": engine.model_id,
                             "choices": [{"index": 0, "delta": {"content": delta},
                                          "finish_reason": None}]}
                    yield f"data: {json.dumps(chunk)}\n\n"
                final_state, final_error = "done", None
            except (ChatModelUnavailable, ChatBusy, ChatContextExceeded) as exc:
                final_state, final_error = "error", "chat_error"
                error = {"error": {"message": str(exc), "type": "chat_error"}}
                yield f"data: {json.dumps(error)}\n\n"
                yield "data: [DONE]\n\n"
                return
            except Exception:
                final_state, final_error = "error", "stream_failed"
                raise
            finally:
                # Closing the engine's generator is the lock release. Without it the model
                # stays reserved by a client that is no longer there.
                stream.close()
                activity.finish(state=final_state, error_code=final_error)
            prompt_tokens = engine.prompt_length(messages)
            final = {"id": completion_id, "object": "chat.completion.chunk", "created": created,
                     "model": engine.model_id,
                     "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                     "usage": _usage(engine, prompt_tokens, "".join(collected))}
            if generation_seconds is not None:
                # The same field, with the same meaning, as the buffered path's: the time the
                # engine spent generating. The Hub records it as `generation_s`; the rate itself
                # is derived where it is displayed, not here.
                final["studio_hub"] = {"elapsed_seconds": generation_seconds}
            yield f"data: {json.dumps(final)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/api/chat/completions")
    def chat_completions_alias(body: dict):
        return chat_completions(body)

    @app.get("/v1/models")
    def list_models() -> dict:
        """OpenAI-shaped model listing, matching the upstream Chat Studio's `/v1/models`."""
        return {"object": "list",
                "data": [{"id": engine.model_id, "object": "model",
                          "created": _MODELS_CREATED, "owned_by": "studio-hub"}]}

    @app.get("/api/chat/models")
    def chat_models() -> dict:
        state = readiness()
        return {"models": [{"repo": engine.model_id, "model_id": engine.model_id,
                            "revision": engine.revision,
                            "cached": state["files"]["state"] == "files_verified",
                            "context_window_tokens": engine.context_window_tokens,
                            "max_output_tokens": engine.max_output_tokens}]}

    @app.get("/api/chat/diagnostics")
    def chat_diagnostics() -> dict:
        """mlx / mlx-lm availability and the loaded model, as upstream publishes it."""
        try:
            import mlx.core as mx
            mlx_version = mx.__version__
        except Exception:
            mlx_version = None
        state = engine.status()
        return {"engine": "mlx-lm", "mlx": mlx_version, "model_id": engine.model_id,
                "revision": engine.revision, "loaded": state["loaded"], "busy": state["busy"],
                "context_window_tokens": engine.context_window_tokens,
                "max_output_tokens": engine.max_output_tokens,
                "active_memory_bytes": state["active_memory_bytes"],
                "peak_memory_bytes": state["peak_memory_bytes"],
                "runtime_revision": runtime_sha256}

    # ── model memory policy ────────────────────────────────────────────────
    @app.get("/api/memory-policy")
    def memory_policy_status() -> dict:
        return policy.status()

    @app.put("/api/memory-policy")
    def memory_policy_save(body: dict | None = None) -> dict:
        try:
            return policy.save((body or {}).get("mode"))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/memory/release")
    def memory_release() -> dict:
        try:
            return policy.release("manual")
        except Exception as exc:
            raise HTTPException(409, str(exc)) from exc

    return app


def from_environment() -> FastAPI:
    engine = engine_from_environment()
    data = Path(os.environ["STUDIOHUB_CHAT_DATA_DIR"])
    release = json.loads(Path(os.environ["STUDIOHUB_RELEASE_MANIFEST"]).read_text())
    identity = release["components"]["chat"]["runtime_sha256"]
    if not isinstance(identity, str) or len(identity) != 64:
        raise ValueError("Release manifest lacks exact Chat runtime identity")
    policy = memory_policy_module.MemoryPolicy(data, engine)
    policy.start()
    return create_app(engine, policy, os.environ["STUDIOHUB_FLEET_TOKEN"], identity)
