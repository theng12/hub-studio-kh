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
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import memory_policy as memory_policy_module
from .llm_engine import ChatBusy, ChatEngine, ChatModelUnavailable, engine_from_environment

# Qwen3.5-9B is the owner-chosen common fleet model and the 16 GB tier is where it is
# practical (the weights alone are ~7.1 GB). The floor is a warning, not a dispatch gate:
# the Hub's admission governor owns whether this host has room right now.
QUALIFIED_MINIMUM_MEMORY_BYTES = 16_000_000_000

# One stable creation stamp for the OpenAI model listing.
_MODELS_CREATED = int(time.time())


def _host_eligibility() -> dict:
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
    state = {"eligible": True, "macos": version, "unified_memory_bytes": memory,
             "qualified_minimum_memory_bytes": QUALIFIED_MINIMUM_MEMORY_BYTES,
             "memory_warning": None,
             "qualification": "16 GB is the owner-chosen practical tier; this host still needs packaged proof"}
    if memory < QUALIFIED_MINIMUM_MEMORY_BYTES:
        state["memory_warning"] = (
            f"This Mac has {memory / 1024**3:.0f} GB of unified memory. Qwen3.5-9B is the "
            "16 GB tier; on a smaller Mac it will swap or fail, especially with another "
            "Studio holding a model.")
    return state


def _usage(engine: ChatEngine, prompt_tokens: int | None, text: str) -> dict:
    completion = engine.count_tokens(text)
    total = None
    if prompt_tokens is not None and completion is not None:
        total = prompt_tokens + completion
    return {"prompt_tokens": prompt_tokens, "completion_tokens": completion,
            "total_tokens": total}


def create_app(engine: ChatEngine, policy: memory_policy_module.MemoryPolicy,
               token: str, runtime_sha256: str) -> FastAPI:
    if not token:
        raise ValueError("Private Chat worker requires its owning Hub credential")
    app = FastAPI(title="Studio Hub Chat", version="1")

    @app.middleware("http")
    async def require_owner(request: Request, call_next):
        offered = (request.headers.get("x-studio-token") or request.headers.get("x-hub-token")
                   or request.headers.get("authorization", "").removeprefix("Bearer "))
        if not hmac.compare_digest(offered, token):
            return JSONResponse({"detail": "Owner credential required"}, status_code=401)
        return await call_next(request)

    def readiness() -> dict:
        files = engine.preflight()
        host = _host_eligibility()
        return {"ready": files["state"] == "files_verified" and host["eligible"],
                "files": files, "host": host,
                "runtime_identity": {"id": runtime_sha256, "compatible": True}}

    @app.get("/api/health")
    def health() -> dict:
        state = engine.status()
        return {"ok": True, "app_version": "standalone", "generation": {
            "available": True, "busy": state["busy"], "queued": 0,
            "running": 1 if state["busy"] else 0, "runtime_revision": runtime_sha256}}

    @app.get("/api/fleet/activity")
    def fleet_activity() -> dict:
        """Valid empty snapshot; Chat has no queue, so it never has an active job here."""
        state = engine.status()
        return {"schema": "kh-studio.activity.v1", "studio": "chat",
                "observed_at": time.time(), "active": None, "latest": None,
                "loaded": state["loaded"]}

    @app.get("/api/catalog")
    def catalog() -> dict:
        state = readiness()
        ready = state["ready"]
        files = state["files"]
        return {"models": [{
            "label": "Qwen3.5-9B", "name": "Qwen3.5-9B",
            "repo": engine.model_id, "model_id": engine.model_id,
            "family": "qwen3.5", "modality": "chat",
            "capabilities": ["chat.completion"], "hub_operation": "chat.completion",
            "model_revision": engine.revision, "qualified_revision": engine.revision,
            "qualified_revision_match": True, "runtime_revision": runtime_sha256,
            "runtime_compatible": True, "runtime_ready": True,
            "verified_token_usage": False,
            "memory_eligible": state["host"]["eligible"],
            "min_total_memory_gb": QUALIFIED_MINIMUM_MEMORY_BYTES / 1e9,
            "qualified_minimum_memory_gb": QUALIFIED_MINIMUM_MEMORY_BYTES / 1e9,
            "memory_warning": state["host"].get("memory_warning"),
            "cache": {"state": "cached" if files["state"] == "files_verified" else files["state"]},
            "readiness": {"ready": ready, "files": files, "host": state["host"],
                          "reason": None if ready else files.get("reason") or state["host"].get("reason")},
            "execution_ready": ready, "available": ready,
            "context_window_tokens": engine.context_window_tokens,
            "max_output_tokens": engine.max_output_tokens,
            "generation_defaults": {"temperature": 0.7, "top_p": 0.8},
            "qualification": {"owner_decision": "Qwen3.5-9B OptiQ 4-bit, MLX, text-only",
                              "packaged_inference": "pending measured proof"},
        }]}

    def _body_model(body: dict) -> None:
        requested = body.get("model")
        if requested and requested != engine.model_id:
            raise HTTPException(404, f"this worker serves only {engine.model_id}")

    @app.post("/v1/chat/completions")
    def chat_completions(body: dict):
        state = readiness()
        if not state["host"]["eligible"]:
            raise HTTPException(409, state["host"]["reason"])
        if not state["ready"]:
            raise HTTPException(409, "Qwen3.5-9B is not installed in the selected model library")
        _body_model(body)
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise HTTPException(422, "messages must be a non-empty list")
        for message in messages:
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                raise HTTPException(422, "every message needs string content")
        max_tokens = body.get("max_tokens") or body.get("max_completion_tokens")
        temperature = float(body.get("temperature", 0.7))
        top_p = float(body.get("top_p", 0.95))
        completion_id = "chatcmpl-" + uuid.uuid4().hex
        created = int(time.time())

        if not body.get("stream"):
            try:
                outcome = engine.complete(messages, max_tokens=max_tokens,
                                          temperature=temperature, top_p=top_p,
                                          response_format=body.get("response_format"),
                                          seed=body.get("seed"),
                                          thinking=bool(body.get("thinking", False)))
            except ChatModelUnavailable as exc:
                raise HTTPException(409, str(exc)) from exc
            except ChatBusy as exc:
                raise HTTPException(409, str(exc)) from exc
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

        def events():
            collected: list[str] = []
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
            except (ChatModelUnavailable, ChatBusy) as exc:
                error = {"error": {"message": str(exc), "type": "chat_error"}}
                yield f"data: {json.dumps(error)}\n\n"
                yield "data: [DONE]\n\n"
                return
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
