"""Private standalone Chat API for one pinned local MLX text model.

Serves the OpenAI-compatible surface the Hub and (later) gateway-kh can call, plus the
readiness, catalog and memory-policy contracts every Studio in this app publishes. It
deliberately has no job queue of its own: a completion is a single request/response, and
the Hub owns admission, dispatch and history.
"""
from __future__ import annotations

import hmac
import json
import os
import platform
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from components import app_version

from . import memory_policy as memory_policy_module
from .llm_engine import ChatBusy, ChatEngine, ChatModelUnavailable, engine_from_environment

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


def _model_label(engine) -> str:
    """The model's own label, tolerant of a stub engine in tests."""
    label = getattr(engine, "label", None)
    if isinstance(label, str) and label.strip():
        return label.strip()
    return str(engine.model_id)


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
    contract cannot go stale against the code. A parameter this worker ignores is deliberately
    absent: a caller that sends one and sees it silently dropped has been lied to.
    """
    limits = engine.model_limits() if hasattr(engine, "model_limits") else {}
    max_output = (limits.get("max_output_tokens") or {}).get("value")
    parameters = [
        {"name": "model", "type": "string", "required": False, "default": engine.model_id,
         "note": "the one model this worker serves; any other value is refused by name"},
        {"name": "messages", "type": "array", "required": True,
         "note": "OpenAI-shaped messages, each with a string content field"},
        {"name": "max_tokens", "type": "integer", "required": False,
         "minimum": 1,
         "note": "max_completion_tokens is accepted as an alias"},
    ]
    if isinstance(max_output, int) and max_output > 0:
        parameters[-1]["maximum"] = max_output
        parameters[-1]["default"] = max_output
    parameters += [
        {"name": "temperature", "type": "number", "required": False, "default": 0.7,
         "minimum": 0.0},
        {"name": "top_p", "type": "number", "required": False, "default": 0.95,
         "minimum": 0.0, "maximum": 1.0},
        {"name": "stream", "type": "boolean", "required": False, "default": False},
        {"name": "response_format", "type": "object", "required": False,
         "note": "type must be json_object or json_schema; anything else is refused"},
        {"name": "seed", "type": "integer", "required": False},
        {"name": "thinking", "type": "boolean", "required": False, "default": False},
    ]
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
    activity = ChatActivity()

    @app.middleware("http")
    async def require_owner(request: Request, call_next):
        offered = (request.headers.get("x-studio-token") or request.headers.get("x-hub-token")
                   or request.headers.get("authorization", "").removeprefix("Bearer "))
        if not hmac.compare_digest(offered, token):
            return JSONResponse({"detail": "Owner credential required"}, status_code=401)
        return await call_next(request)

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
            "generation_defaults": {"temperature": 0.7, "top_p": 0.8},
            # The dispatch contract, published by the only party that knows this API. The Hub
            # passes it through verbatim and adds its own `deadline_seconds`.
            "lane_contract": _lane_contract(engine),
            "qualification": {"owner_decision": f"{_model_label(engine)}, MLX, text-only",
                              "packaged_inference": "pending measured proof"},
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

    def _completion_parameters(body: dict) -> tuple[int | None, float, float]:
        """Validate the asked-for surface, refusing rather than silently dropping a value."""
        max_tokens = body.get("max_tokens")
        if max_tokens is None:
            max_tokens = body.get("max_completion_tokens")
        if max_tokens is not None:
            if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
                raise HTTPException(422, "max_tokens must be a positive integer")
        try:
            temperature = float(body.get("temperature", 0.7))
            top_p = float(body.get("top_p", 0.95))
        except (TypeError, ValueError):
            raise HTTPException(422, "temperature and top_p must be numbers") from None
        if temperature < 0:
            raise HTTPException(422, "temperature must not be negative")
        if not 0.0 < top_p <= 1.0:
            raise HTTPException(422, "top_p must be greater than 0 and at most 1")
        response_format = body.get("response_format")
        if response_format is not None:
            if not isinstance(response_format, dict):
                raise HTTPException(422, "response_format must be an object")
            kind = str(response_format.get("type") or "")
            if kind not in {"json_object", "json_schema"}:
                raise HTTPException(
                    422, "response_format.type must be json_object or json_schema")
        return max_tokens, temperature, top_p

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
        max_tokens, temperature, top_p = _completion_parameters(body)
        completion_id = _job_identifier(body)
        created = int(time.time())

        if not body.get("stream"):
            activity.begin(job_id=completion_id, model=engine.model_id)
            try:
                outcome = engine.complete(messages, max_tokens=max_tokens,
                                          temperature=temperature, top_p=top_p,
                                          response_format=body.get("response_format"),
                                          seed=body.get("seed"),
                                          thinking=bool(body.get("thinking", False)))
            except ChatModelUnavailable as exc:
                activity.finish(state="error", error_code="model_unavailable")
                raise HTTPException(409, str(exc)) from exc
            except ChatBusy as exc:
                activity.finish(state="error", error_code="worker_busy")
                raise HTTPException(409, str(exc)) from exc
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

        def events():
            collected: list[str] = []
            # The terminal state this request reports, unless a branch below changes it. The
            # `finally` is what handles a client that disconnects mid-stream: the generation was
            # never delivered, so it is cancelled rather than claimed as a completion.
            final_state, final_error = "cancelled", "client_disconnected"
            try:
                for delta in engine.stream(messages, max_tokens=max_tokens,
                                           temperature=temperature, top_p=top_p,
                                           response_format=body.get("response_format"),
                                           seed=body.get("seed"),
                                           thinking=bool(body.get("thinking", False))):
                    if not delta:
                        continue
                    collected.append(delta)
                    chunk = {"id": completion_id, "object": "chat.completion.chunk",
                             "created": created, "model": engine.model_id,
                             "choices": [{"index": 0, "delta": {"content": delta},
                                          "finish_reason": None}]}
                    yield f"data: {json.dumps(chunk)}\n\n"
                final_state, final_error = "done", None
            except (ChatModelUnavailable, ChatBusy) as exc:
                final_state, final_error = "error", "chat_error"
                error = {"error": {"message": str(exc), "type": "chat_error"}}
                yield f"data: {json.dumps(error)}\n\n"
                yield "data: [DONE]\n\n"
                return
            except Exception:
                final_state, final_error = "error", "stream_failed"
                raise
            finally:
                activity.finish(state=final_state, error_code=final_error)
            prompt_tokens = engine.prompt_length(messages)
            final = {"id": completion_id, "object": "chat.completion.chunk", "created": created,
                     "model": engine.model_id,
                     "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                     "usage": _usage(engine, prompt_tokens, "".join(collected))}
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
