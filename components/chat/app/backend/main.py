"""Private standalone Chat API for one pinned local MLX text model.

Serves the OpenAI-compatible surface the Hub and (later) gateway-kh can call, plus the
readiness, catalog and memory-policy contracts every Studio in this app publishes. It
deliberately has no job queue of its own: a completion is a single request/response, and
the Hub owns admission, dispatch and history.
"""
from __future__ import annotations

import hmac
import inspect
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

from components import app_version, model_defaults

from . import memory_policy as memory_policy_module
from .llm_engine import (DEFAULT_TEMPERATURE, DEFAULT_TOP_P, FALLBACK_MAX_OUTPUT_TOKENS,
                         ChatBusy, ChatContextExceeded, ChatEngine, ChatModelUnavailable,
                         ChatStructuredOutputError, ChatTemplateError)
from .model_manager import ChatModelManager

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


def _accepts_progress(method) -> bool:
    """Check old fixture compatibility without retrying a possibly-started generation."""
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD
               or parameter.name == "on_progress" for parameter in parameters)


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


def _finish_reason(telemetry) -> str:
    if isinstance(telemetry, dict):
        final_attempt = telemetry.get("final_attempt")
        attempts = telemetry.get("attempts")
        if isinstance(attempts, list):
            for attempt in reversed(attempts):
                if (isinstance(attempt, dict) and attempt.get("attempt") == final_attempt
                        and isinstance(attempt.get("finish_reason"), str)
                        and attempt["finish_reason"]):
                    return attempt["finish_reason"]
    return "stop"


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
        # The 8 GB tier is not a tight fit to be attempted, it is an exclusion, and the sentence
        # now says so. It used to read "it will swap or fail", which invited the attempt: the owner
        # did attempt it, it does not run, and each try costs up to the harness's 900 s timeout plus
        # a load that swaps the SSD. A warning that understates a known exclusion is the reason he
        # spent that time.
        state["memory_warning"] = (
            f"This Mac has {memory / 1024**3:.0f} GB of unified memory, and {label} does not run "
            f"here. It is qualified at {qualified_bytes / 1e9:g} GB and excluded below it: the "
            "weights need working room on top that this Mac does not have. Run it on a 16 GB or "
            "larger Mac. This is a decision from a measured tier, not a caution.")
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
        self._active: dict[tuple[str, str], dict] = {}
        self._latest: dict | None = None

    def begin(self, *, job_id: str, model: str) -> None:
        now = time.time()
        with self._lock:
            self._active[(job_id, model)] = {
                "id": job_id, "state": "running", "model": model,
                "source": "direct", "origin": "unknown",
                "created_at": now, "started_at": now, "updated_at": now,
                "progress_stage": "waiting",
            }

    def progress(self, *, job_id: str, model: str, event: dict) -> None:
        allowed = {
            "attempt", "processed_prompt_tokens", "total_prompt_tokens",
            "prompt_tokens", "prompt_tps", "generation_tokens", "generation_tps",
            "peak_memory_bytes", "finish_reason",
        }
        with self._lock:
            active = self._active.get((job_id, model))
            if active is None:
                return
            update = {key: value for key, value in event.items() if key in allowed}
            previous_attempt = active.get("attempt")
            attempt = update.get("attempt", previous_attempt)
            if (previous_attempt is not None and attempt != previous_attempt):
                for key in (*allowed, "progress"):
                    active.pop(key, None)
            processed = update.get("processed_prompt_tokens")
            if (attempt == previous_attempt and isinstance(processed, int)
                    and isinstance(active.get("processed_prompt_tokens"), int)
                    and processed < active["processed_prompt_tokens"]):
                update.pop("processed_prompt_tokens", None)
            active.update(update)
            phase = event.get("phase")
            if phase in {"waiting", "loading", "processing", "generating", "completed"}:
                active["progress_stage"] = phase
            processed = active.get("processed_prompt_tokens")
            total = active.get("total_prompt_tokens")
            if (isinstance(processed, int) and isinstance(total, int) and total > 0
                    and 0 <= processed <= total):
                active["progress"] = processed / total
            else:
                active.pop("progress", None)
            active["updated_at"] = time.time()

    def finish(self, *, job_id: str, model: str, state: str,
               error_code: str | None = None) -> None:
        now = time.time()
        with self._lock:
            key = (job_id, model)
            active = self._active.get(key)
            if active is None:
                return
            self._active.pop(key, None)
            started = float(active.get("started_at") or now)
            latest = {
                **active, "state": state, "updated_at": now, "finished_at": now,
                "runtime_s": round(max(0.0, now - started), 3),
            }
            latest.pop("progress", None)
            if error_code:
                latest["error_code"] = error_code
            self._latest = latest

    def snapshot(self, engine, *, job_id: str | None = None) -> dict:
        with self._lock:
            if job_id is not None:
                active = next(
                    (dict(item) for item in self._active.values() if item.get("id") == job_id),
                    None,
                )
                latest = (dict(self._latest)
                          if self._latest and self._latest.get("id") == job_id else None)
            else:
                running = next(
                    (item for item in self._active.values()
                     if item.get("progress_stage") in {"loading", "processing", "generating"}),
                    None,
                )
                chosen = running or (next(iter(self._active.values())) if self._active else None)
                active = dict(chosen) if chosen else None
                latest = dict(self._latest) if self._latest else None
        return {
            "schema": ACTIVITY_SCHEMA, "studio": ACTIVITY_STUDIO,
            "observed_at": time.time(), "active": active, "latest": latest,
            "loaded": bool(engine.loaded()),
        }


def _lane_output_budget(output_limit: int | None) -> int:
    """The output budget this lane uses when a request omits ``max_tokens``.

    **The worker's own fallback, not the model's ceiling.** A ceiling is the largest generation a
    model allows, not a preference, and publishing it as the default would hand every caller that
    omitted the field the biggest window the model has — one blanket number applied to every model
    that happens to share this worker. Clamped to the model's ceiling where a model states less
    than the fallback, so the contract can never advertise a default the route would refuse.
    """
    if output_limit is None:
        return FALLBACK_MAX_OUTPUT_TOKENS
    return min(FALLBACK_MAX_OUTPUT_TOKENS, output_limit)


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
    output_default = _lane_output_budget(output_limit)
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

    # Where the lane's own output budget comes from, said in the contract rather than left for a
    # reader to guess: it is this worker's fallback unless a model states a smaller ceiling.
    if output_limit is not None and output_limit < FALLBACK_MAX_OUTPUT_TOKENS:
        budget_origin = (f"the ceiling this model states, which is below this worker's own "
                         f"{FALLBACK_MAX_OUTPUT_TOKENS}-token fallback budget "
                         f"(llm_engine.FALLBACK_MAX_OUTPUT_TOKENS)")
    else:
        budget_origin = (f"this worker's own fallback output budget "
                         f"(llm_engine.FALLBACK_MAX_OUTPUT_TOKENS = "
                         f"{FALLBACK_MAX_OUTPUT_TOKENS}), which is not a number the model states")

    parameters = [
        # The one value this worker serves. Published as an `enum` bound as well as a default so
        # a stored default cannot name a model the route would refuse: this parameter is in the
        # defaults surface (it is not `required` - a caller may omit it and be served the model
        # this worker runs), and a free string there would let the catalog advertise a `model`
        # whose every request is a 404.
        {"name": "model", "type": "string", "required": False, "default": engine.model_id,
         "enum": [engine.model_id],
         "note": "the one model this worker serves; any other value is refused by name"},
        {"name": "messages", "type": "array", "required": True, "note": prompt_note},
        {"name": "max_tokens", "type": "integer", "required": False, "minimum": 1,
         "default": output_default,
         "note": ("max_completion_tokens is accepted as an alias; a value above the published "
                  f"maximum is refused by name. Omitted, this lane uses {output_default}: "
                  f"{budget_origin}.")},
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
        entry["note"] += f" This model's ceiling is {output_limit} ({output_source})."
    return parameters


def _resolved_parameters(data_dir, engine) -> tuple[list[dict], dict]:
    """``(parameters, values in force)`` for one model, from one ``resolved()`` call.

    Shared by the completion route and the catalog on purpose: the value the catalog publishes in
    a parameter's ``default`` is read from the same resolution the route serves with, so the
    Models tab cannot be shown a default the worker would refuse or ignore. A stored value is
    validated on the way out by :mod:`components.model_defaults`, so a model update that tightens
    a bound drops the stored number rather than turning it into a refused request.
    """
    parameters = _parameter_surface(engine)
    in_force, _not_used = model_defaults.resolved(data_dir, engine.model_id, parameters)
    published = [{**parameter, "default": in_force[str(parameter["name"])]}
                 if str(parameter["name"]) in in_force else parameter
                 for parameter in parameters]
    return published, in_force


def _refusals_from_save(exc: ValueError):
    """The per-parameter reasons ``model_defaults.save`` refused with.

    ``save`` raises ``ValueError`` carrying a JSON object of them, so the route answers with every
    problem at once instead of the first one. A message that is not that object is passed through
    as the detail rather than swallowed, because a refusal nobody can read is worse than none.
    """
    try:
        detail = json.loads(str(exc))
    except ValueError:
        return str(exc)
    return detail if isinstance(detail, dict) else str(exc)


def _lane_contract(engine, parameters: list[dict]) -> dict:
    """This lane's dispatch contract: what to send, what comes back, what a cancel promises.

    ``parameters`` is the lane's own published surface with the value in force in each
    ``default`` (``_resolved_parameters``), passed in rather than rebuilt here so this contract
    and the completion route cannot answer the same question twice.

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
        "parameters": parameters,
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
        # How this lane enforces a structured answer, as a fact rather than as prose. A consumer
        # deciding whether a strict-JSON call site is safe here — Story Studio's outline,
        # thumbnail-prompt and metadata contracts are exactly that — needs to know *how* the JSON
        # guarantee is kept, and "json_object is supported" does not say it. This runtime has no
        # grammar-constrained decoding, so the guarantee is a prompt, a validation and one repair
        # re-ask; a caller assuming a grammar would treat a repaired answer as a first-try one and
        # pay for two generations without knowing.
        #
        # Absent on every other lane, and absent means "not applicable": only a text lane has a
        # structured-output guarantee to describe. A lane that gains one publishes this field
        # rather than a sentence, because a consumer can match a field and cannot match prose.
        "structured_output": {
            "formats": ["json_object", "json_schema"],
            "enforcement": "prompt_validate_repair",
            "grammar_constrained": False,
            "repair_attempts": 1,
            "streaming": "buffered_until_validated",
            "note": ("the format is requested in the prompt, the answer is validated, and one "
                     "repair re-ask is made when it does not parse; there is no "
                     "grammar-constrained decoding in this runtime, so a caller should validate "
                     "too and should expect a repaired answer to have cost a second generation"),
        },
    }


class _SingleEngineCatalog:
    """Compatibility for existing callers and fake fixtures that pass one engine."""

    def __init__(self, engine) -> None:
        self.models = (engine,)
        self.default = engine

    def for_model(self, model_id: str | None):
        if model_id in (None, "", self.default.model_id):
            return self.default
        raise KeyError(model_id)

    def loaded(self) -> bool:
        return bool(self.default.loaded())

    def status(self) -> dict:
        return self.default.status()


def create_app(engine: ChatEngine | ChatModelManager, policy: memory_policy_module.MemoryPolicy,
               token: str, runtime_sha256: str,
               data_dir: str | os.PathLike[str] | None = None) -> FastAPI:
    if not token:
        raise ValueError("Private Chat worker requires its owning Hub credential")
    # This worker's own state directory: `memory_policy.json` already lives there, and a
    # per-model default belongs beside it - per machine, because a default is a statement about
    # how this operator wants this Mac to work. The policy is the one place the directory is
    # configured, so the fallback is the policy's own answer rather than a second path.
    settings_dir = Path(data_dir) if data_dir is not None else Path(policy.data_dir)
    app = FastAPI(title="Studio Hub Chat", version="1")
    app.add_middleware(_OwnerCredentialMiddleware, token=token)
    activity = ChatActivity()
    model_manager = engine if isinstance(engine, ChatModelManager) else _SingleEngineCatalog(engine)
    engine = model_manager.default

    def request_usage(stated, *, prompt_tokens=None, text="") -> dict:
        if isinstance(stated, dict):
            return stated
        if isinstance(model_manager, ChatModelManager):
            # Managed proxies always capture tokenizer counts before releasing their session.
            # If an older managed engine omits them, keep the omission rather than reload or
            # count with whichever model became resident next.
            return {"prompt_tokens": prompt_tokens, "completion_tokens": None,
                    "total_tokens": None}
        return _usage(engine, prompt_tokens, text)

    def readiness(selected) -> dict:
        files = selected.preflight()
        host = _host_eligibility(selected)
        allowed = getattr(selected, "production_allowed", True)
        reason = getattr(selected, "production_refusal", None)
        return {"ready": allowed and files["state"] == "files_verified" and host["eligible"],
                "files": files, "host": host,
                "production_allowed": allowed, "production_refusal": reason,
                "runtime_identity": {"id": runtime_sha256, "compatible": True}}

    @app.get("/api/health")
    def health() -> dict:
        state = model_manager.status()
        return {"ok": True, **app_version.version_fields(), "generation": {
            "available": True, "busy": state["busy"], "queued": 0,
            "running": 1 if state["busy"] else 0, "runtime_revision": runtime_sha256}}

    @app.get("/api/fleet/activity")
    def fleet_activity(job_id: str | None = None) -> dict:
        """This worker's own job state, in the Hub's allowlisted activity contract.

        Chat has no queue, so an idle worker reports an empty snapshot rather than a fabricated
        job. While a completion is running, the request it is serving is the active item; the
        last completed request is the latest one. Before this, the route answered
        ``{"active": None, "latest": None}`` unconditionally, so Chat work appeared in no
        activity view anywhere even after the Hub learned to read the chat queue.
        """
        return activity.snapshot(model_manager, job_id=job_id)

    @app.get("/api/catalog")
    def catalog() -> dict:
        rows = []
        for selected in model_manager.models:
            state = readiness(selected)
            ready = state["ready"]
            files = state["files"]
            allowed = state["production_allowed"]
            qualified_bytes = _qualified_minimum_memory_bytes(selected) if allowed else None
            qualified_gb = qualified_bytes / 1e9 if qualified_bytes else None
            parameters, in_force = _resolved_parameters(settings_dir, selected)
            qualification = (getattr(selected, "manifest", {}) or {}).get("qualification") or {}
            inference_evidence = qualification.get("reason") or (
                "Production allowed by bundled policy; no model-specific inference evidence is declared.")
            row = {
            # The model's own label, read from its manifest/repo id - never a literal here, so
            # a second chat model cannot be published under the first one's name. It carries the
            # quantization, which is what a reader needs to check it against what was measured.
            "label": _model_label(selected), "name": _model_label(selected),
            "repo": selected.model_id, "model_id": selected.model_id,
            "family": getattr(selected, "family", None), "modality": "chat",
            "capabilities": ["chat.completion"], "hub_operation": "chat.completion",
            "model_revision": selected.revision,
            "qualified_revision": selected.revision if allowed else None,
            "qualified_revision_match": True if allowed else False,
            "runtime_revision": runtime_sha256,
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
                          "production_allowed": allowed,
                          "reason": (None if ready else state["production_refusal"]
                                     or files.get("reason") or state["host"].get("reason"))},
            "execution_ready": ready, "available": ready,
            "context_window_tokens": selected.context_window_tokens,
            "max_output_tokens": selected.max_output_tokens,
            # Every limit with the source that states it, so a reader can label its own fallback
            # instead of inheriting this worker's number as the model's fact.
            "model_limits": selected.model_limits() if hasattr(selected, "model_limits") else None,
            # The defaults the route actually serves with: the model's own stated default for a
            # parameter nobody has chosen, the operator's stored value where one exists, and the
            # engine's constant where the lane states none. Read from the same resolution as the
            # lane contract below, so the two cannot disagree.
            "generation_defaults": {
                "temperature": in_force.get("temperature", DEFAULT_TEMPERATURE),
                "top_p": in_force.get("top_p", DEFAULT_TOP_P),
            },
            # The dispatch contract, published by the only party that knows this API. The Hub
            # passes it through verbatim and adds its own `deadline_seconds`.
            "lane_contract": _lane_contract(selected, parameters),
            "qualification": {
                "owner_decision": f"{_model_label(selected)}, MLX, text-only",
                "packaged_inference": (inference_evidence if allowed else
                                       "pending: production inference is blocked until qualification"),
            },
            "default": selected is model_manager.default,
            "production_allowed": allowed,
            }
            rows.append(row)
        return {"models": rows}

    def _job_identifier(body: dict) -> str:
        """The id this request is tracked under: the Hub's, when it named one."""
        requested = body.get("job_id")
        if isinstance(requested, str) and _JOB_ID.fullmatch(requested.strip()):
            return requested.strip()
        return "chatcmpl-" + uuid.uuid4().hex

    def _body_model(body: dict):
        requested = body.get("model")
        try:
            return model_manager.for_model(requested)
        except KeyError as exc:
            offered = ", ".join(model.model_id for model in model_manager.models)
            raise HTTPException(404, (
                f"unknown Chat model {requested!r}; this worker serves {offered}. Add its "
                "validated chat-mlx manifest to the bundled model-manifests directory selected "
                "by STUDIOHUB_MODEL_MANIFESTS.")) from exc

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

    def _completion_parameters(body: dict, defaults: dict, selected=None) -> dict:
        """Validate the asked-for surface, refusing rather than silently dropping a value.

        ``defaults`` is what is in force for this model - the operator's stored values over the
        lane's own - and it is applied **only where the request omits the parameter**. A value
        the request supplies always wins: a default is where a request starts, never an override
        of one, or a saved number would quietly outrank the caller that asked for something else.
        """
        selected = selected or engine
        _refuse_unsupported_fields(body)
        max_tokens = body.get("max_tokens")
        if max_tokens is None:
            max_tokens = body.get("max_completion_tokens")
        if max_tokens is None:
            max_tokens = defaults.get("max_tokens")
        if max_tokens is not None:
            if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
                raise HTTPException(
                    422, f"max_tokens: must be an integer of at least 1; received "
                         f"{_describe(max_tokens)}")
            limit, source = _published_max_output_tokens(selected)
            if limit is not None and max_tokens > limit:
                raise HTTPException(422, (
                    f"max_tokens: {max_tokens} is above the maximum this lane publishes "
                    f"({limit}, {source}). Send at most {limit}."))
        temperature = _number(body, "temperature",
                              defaults.get("temperature", DEFAULT_TEMPERATURE))
        if temperature < 0:
            raise HTTPException(
                422, f"temperature: must not be negative; received {temperature!r}")
        top_p = _number(body, "top_p", defaults.get("top_p", DEFAULT_TOP_P))
        if not 0.0 < top_p <= 1.0:
            raise HTTPException(
                422, f"top_p: must be greater than 0 and at most 1; received {top_p!r}")
        response_format = body.get("response_format", defaults.get("response_format"))
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
        seed = body.get("seed", defaults.get("seed"))
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise HTTPException(
                422, f"seed: must be an integer or omitted; received {_describe(seed)}")
        thinking = body.get("thinking", defaults.get("thinking", False))
        if not isinstance(thinking, bool):
            # `bool("false")` is True in Python, so the old coercion turned thinking ON for a
            # caller that asked for it off. Only a real JSON boolean is accepted.
            raise HTTPException(422, (
                f"thinking: must be a JSON boolean (true or false); received "
                f"{_describe(thinking)}. A string is refused rather than coerced, because "
                f"bool(\"false\") is true and would turn thinking ON."))
        return {"max_tokens": max_tokens, "temperature": temperature, "top_p": top_p,
                "response_format": response_format, "seed": seed, "thinking": thinking}

    def _stream_requested(body: dict, defaults: dict) -> bool:
        """`stream` is a real JSON boolean too, for the same reason `thinking` is.

        A truthy string such as ``"false"`` would otherwise start an SSE response for a caller
        that asked for one JSON completion.
        """
        value = body.get("stream", defaults.get("stream", False))
        if not isinstance(value, bool):
            raise HTTPException(422, (
                f"stream: must be a JSON boolean (true or false); received {_describe(value)}. "
                f"A non-boolean is refused rather than coerced."))
        return value

    def _route_refusals(values: dict, selected=None) -> dict[str, str]:
        """The proposed values this lane's own completion route would refuse, by parameter.

        :mod:`components.model_defaults` checks a value against the bound the lane *publishes*;
        this checks it against the code that *enforces* it, which is the rule that module exists
        to apply. Three of this lane's refusals are not numeric bounds and would otherwise reach
        the defaults file: ``top_p``'s minimum is exclusive (0 is refused although the published
        minimum is 0.0), a non-finite number reaches the sampler as NaN/Infinity, and
        ``response_format.type`` must be a JSON mode this engine can serve. A stored value of any
        of them would turn every later request that omitted that parameter into a 422.
        """
        refusals: dict[str, str] = {}
        for name, value in values.items():
            try:
                if name == "stream":
                    _stream_requested({name: value}, {})
                else:
                    _completion_parameters({name: value, "messages": []}, {}, selected)
            except HTTPException as exc:
                refusals[str(name)] = str(exc.detail)
        return refusals

    def _defaults_repo(payload: dict, query_repo: str | None):
        """The model a defaults write names, refused by name when it is not the one served.

        A write for a model this worker does not serve is a refusal rather than a silent no-op:
        stored under a repo nothing will ever read, it would look exactly like a setting that
        took effect.
        """
        offered = payload.get("repo")
        if isinstance(offered, str) and offered.strip():
            repo = offered.strip()
        elif isinstance(query_repo, str) and query_repo.strip():
            repo = query_repo.strip()
        else:
            raise HTTPException(422, (
                f"repo: this route needs the model the defaults are for; received "
                f"{_describe(offered)}. This worker serves "
                f"{', '.join(model.model_id for model in model_manager.models)}."))
        try:
            return model_manager.for_model(repo)
        except KeyError as exc:
            offered = ", ".join(model.model_id for model in model_manager.models)
            raise HTTPException(
                404, f"repo: this worker has no defaults for {repo!r}; it serves {offered}") from exc

    def _defaults_status(selected, parameters: list[dict]) -> dict:
        return model_defaults.status(settings_dir, [(selected.model_id, parameters)])

    @app.post("/v1/chat/completions")
    def chat_completions(body: dict):
        selected = _body_model(body)
        state = readiness(selected)
        if not state["host"]["eligible"]:
            raise HTTPException(409, state["host"]["reason"])
        if not state["production_allowed"]:
            raise HTTPException(
                409, f"{_model_label(selected)} is not qualified for production inference: "
                     f"{state['production_refusal']}")
        if not state["ready"]:
            raise HTTPException(
                409, f"{_model_label(selected)} is not installed in the selected model library")
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise HTTPException(422, "messages must be a non-empty list")
        for message in messages:
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                raise HTTPException(422, "every message needs string content")
        # The same `resolved()` call the catalog publishes its `default` fields from: a parameter
        # the request omits takes the value in force here, and one the request supplies wins.
        _published, defaults = _resolved_parameters(settings_dir, selected)
        parameters = _completion_parameters(body, defaults, selected)
        stream = _stream_requested(body, defaults)
        completion_id = _job_identifier(body)
        created = int(time.time())

        completed: dict[str, object] = {}

        def on_progress(event: dict) -> None:
            activity.progress(job_id=completion_id, model=selected.model_id, event=event)
            if event.get("phase") == "completed":
                if isinstance(event.get("telemetry"), dict):
                    completed["telemetry"] = event["telemetry"]
                if isinstance(event.get("usage"), dict):
                    completed["usage"] = event["usage"]

        structured_stream = stream and parameters.get("response_format") is not None
        if not stream or structured_stream:
            activity.begin(job_id=completion_id, model=selected.model_id)
            try:
                if _accepts_progress(selected.complete):
                    outcome = selected.complete(messages, on_progress=on_progress, **parameters)
                else:
                    outcome = selected.complete(messages, **parameters)
            except ChatModelUnavailable as exc:
                activity.finish(job_id=completion_id, model=selected.model_id,
                                state="error", error_code="model_unavailable")
                raise HTTPException(409, str(exc)) from exc
            except ChatBusy as exc:
                activity.finish(job_id=completion_id, model=selected.model_id,
                                state="error", error_code="worker_busy")
                raise HTTPException(409, str(exc)) from exc
            except ChatContextExceeded as exc:
                activity.finish(job_id=completion_id, model=selected.model_id,
                                state="error", error_code="context_exceeded")
                raise HTTPException(422, str(exc)) from exc
            except ChatTemplateError as exc:
                activity.finish(job_id=completion_id, model=selected.model_id,
                                state="error", error_code="template_rejected")
                raise HTTPException(422, str(exc)) from exc
            except ChatStructuredOutputError as exc:
                activity.finish(job_id=completion_id, model=selected.model_id,
                                state="error", error_code="structured_output_failed")
                raise HTTPException(422, str(exc)) from exc
            except Exception:
                activity.finish(job_id=completion_id, model=selected.model_id,
                                state="error", error_code="generation_failed")
                raise
            activity.finish(job_id=completion_id, model=selected.model_id, state="done")
            studio_hub = {"elapsed_seconds": outcome["elapsed_seconds"],
                          "json_valid": outcome.get("json_valid"),
                          "repairs": outcome.get("repairs")}
            telemetry = outcome.get("telemetry")
            if isinstance(telemetry, dict):
                studio_hub["telemetry"] = telemetry
            usage = outcome.get("usage")
            prompt_tokens = outcome.get("prompt_tokens")
            if prompt_tokens is None and not isinstance(model_manager, ChatModelManager):
                prompt_tokens = selected.prompt_length(messages)
            usage = request_usage(usage, prompt_tokens=prompt_tokens, text=outcome["text"])
            finish_reason = _finish_reason(telemetry)
            response = {"id": completion_id, "object": "chat.completion", "created": created,
                    "model": selected.model_id,
                    "choices": [{"index": 0, "finish_reason": finish_reason,
                                 "message": {"role": "assistant", "content": outcome["text"]}}],
                    "usage": usage,
                    "studio_hub": studio_hub}
            if not structured_stream:
                return response

            async def structured_events():
                chunk = {"id": completion_id, "object": "chat.completion.chunk",
                         "created": created, "model": selected.model_id,
                         "choices": [{"index": 0,
                                      "delta": {"content": outcome["text"]},
                                      "finish_reason": None}]}
                yield f"data: {json.dumps(chunk)}\n\n"
                final = {"id": completion_id, "object": "chat.completion.chunk",
                         "created": created, "model": selected.model_id,
                         "choices": [{"index": 0, "delta": {},
                                      "finish_reason": finish_reason}],
                         "usage": usage, "studio_hub": studio_hub}
                yield f"data: {json.dumps(final)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(structured_events(), media_type="text/event-stream")

        activity.begin(job_id=completion_id, model=selected.model_id)

        # A streaming response has already committed its 200 by the time the engine produces a
        # token, so a prompt that cannot fit the model's stated context is refused here, before
        # the SSE response starts.
        check_prompt_fits = getattr(selected, "check_prompt_fits", None)
        if callable(check_prompt_fits):
            try:
                preflight = {
                    "max_tokens": parameters["max_tokens"],
                    "response_format": parameters["response_format"],
                    "thinking": parameters["thinking"],
                }
                if _accepts_progress(check_prompt_fits):
                    preflight["on_progress"] = on_progress
                check_prompt_fits(messages, **preflight)
            except ChatContextExceeded as exc:
                activity.finish(job_id=completion_id, model=selected.model_id,
                                state="error", error_code="context_exceeded")
                raise HTTPException(422, str(exc)) from exc
            except ChatTemplateError as exc:
                activity.finish(job_id=completion_id, model=selected.model_id,
                                state="error", error_code="template_rejected")
                raise HTTPException(422, str(exc)) from exc
            except ChatModelUnavailable as exc:
                activity.finish(job_id=completion_id, model=selected.model_id,
                                state="error", error_code="model_unavailable")
                raise HTTPException(409, str(exc)) from exc
            except ChatBusy as exc:
                activity.finish(job_id=completion_id, model=selected.model_id,
                                state="error", error_code="worker_busy")
                raise HTTPException(409, str(exc)) from exc
            except Exception:
                activity.finish(job_id=completion_id, model=selected.model_id,
                                state="error", error_code="generation_failed")
                raise

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
            if _accepts_progress(selected.stream):
                stream = selected.stream(messages, on_progress=on_progress, **parameters)
            else:
                stream = selected.stream(messages, **parameters)
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
                             "created": created, "model": selected.model_id,
                             "choices": [{"index": 0, "delta": {"content": delta},
                                          "finish_reason": None}]}
                    yield f"data: {json.dumps(chunk)}\n\n"
                final_state, final_error = "done", None
            except (ChatModelUnavailable, ChatBusy, ChatContextExceeded, ChatTemplateError) as exc:
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
                activity.finish(job_id=completion_id, model=selected.model_id,
                                state=final_state, error_code=final_error)
            usage = completed.get("usage")
            if not isinstance(usage, dict) and not isinstance(model_manager, ChatModelManager):
                prompt_tokens = selected.prompt_length(messages)
            else:
                prompt_tokens = None
            usage = request_usage(usage, prompt_tokens=prompt_tokens,
                                  text="".join(collected))
            telemetry = completed.get("telemetry")
            final = {"id": completion_id, "object": "chat.completion.chunk", "created": created,
                     "model": selected.model_id,
                     "choices": [{"index": 0, "delta": {},
                                  "finish_reason": _finish_reason(telemetry)}],
                     "usage": usage}
            if generation_seconds is not None or isinstance(telemetry, dict):
                # The same field, with the same meaning, as the buffered path's: the time the
                # engine spent generating. The Hub records it as `generation_s`; the rate itself
                # is derived where it is displayed, not here.
                final["studio_hub"] = {}
                if generation_seconds is not None:
                    final["studio_hub"]["elapsed_seconds"] = generation_seconds
                if isinstance(telemetry, dict):
                    final["studio_hub"]["telemetry"] = telemetry
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
                "data": [{"id": selected.model_id, "object": "model",
                          "created": _MODELS_CREATED, "owned_by": "studio-hub"}
                         for selected in model_manager.models]}

    @app.get("/api/chat/models")
    def chat_models() -> dict:
        return {"models": [{"repo": selected.model_id, "model_id": selected.model_id,
                            "revision": selected.revision,
                            "cached": readiness(selected)["files"]["state"] == "files_verified",
                            "context_window_tokens": selected.context_window_tokens,
                            "max_output_tokens": selected.max_output_tokens,
                            "default": selected is model_manager.default,
                            "production_allowed": getattr(selected, "production_allowed", True)}
                           for selected in model_manager.models]}

    @app.get("/api/chat/diagnostics")
    def chat_diagnostics() -> dict:
        """mlx / mlx-lm availability and the loaded model, as upstream publishes it."""
        try:
            import mlx.core as mx
            mlx_version = mx.__version__
        except Exception:
            mlx_version = None
        state = model_manager.status()
        loaded_id = state.get("model_id") or engine.model_id
        loaded = model_manager.for_model(loaded_id)
        return {"engine": "mlx-lm", "mlx": mlx_version, "model_id": loaded_id,
                "revision": loaded.revision, "loaded": state["loaded"], "busy": state["busy"],
                "context_window_tokens": loaded.context_window_tokens,
                "max_output_tokens": loaded.max_output_tokens,
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

    # ── per-model operator defaults ────────────────────────────────────────
    # The owner's rule: a default belongs to one model, because each model is weighted
    # differently and a blanket default meant for one model applied to all of them is how a
    # 4096-token chat budget ends up standing in for a model that states 262144.
    @app.get("/api/model-defaults")
    def model_defaults_status() -> dict:
        """What this model accepts, what the lane itself states, and what the operator chose.

        The offered surface is the lane contract's own `parameters` list, so a Models tab renders
        controls from the bounds the route enforces and a parameter this lane refuses is absent
        rather than offered. `defaults` is what a request that omits the parameter is served
        with; `chosen` is only what the operator actually set.
        """
        return model_defaults.status(
            settings_dir,
            [(selected.model_id, _parameter_surface(selected))
             for selected in model_manager.models],
        )

    @app.put("/api/model-defaults")
    def model_defaults_save(body: dict | None = None) -> dict:
        """Store this model's defaults, replaced wholesale, refusing every value by name.

        Nothing is written while any value stands refused, so a save that answers 422 leaves the
        previous defaults exactly as they were instead of half-applying.
        """
        payload = body if isinstance(body, dict) else {}
        selected = _defaults_repo(payload, None)
        repo = selected.model_id
        values = payload.get("values")
        if values is None:
            values = {}
        if not isinstance(values, dict):
            raise HTTPException(422, (
                f"values: must be an object of parameter names to values; received "
                f"{_describe(values)}"))
        parameters = _parameter_surface(selected)
        offered = {str(parameter["name"]) for parameter in model_defaults.editable(parameters)}
        # The lane's published bound first, then the lane's own enforcement: every refusal is
        # reported at once, by parameter, and the published-bound wording wins where both refuse
        # the same value, because that is the number the caller can read in the contract.
        _accepted, refusals = model_defaults.validate(parameters, values)
        for name, reason in _route_refusals(
                {name: value for name, value in values.items() if name in offered}, selected).items():
            refusals.setdefault(name, reason)
        if refusals:
            raise HTTPException(422, refusals)
        try:
            model_defaults.save(settings_dir, repo, values, parameters)
        except ValueError as exc:
            raise HTTPException(422, _refusals_from_save(exc)) from exc
        return _defaults_status(selected, parameters)

    @app.delete("/api/model-defaults")
    def model_defaults_clear(body: dict | None = None, repo: str | None = None) -> dict:
        """Forget one model's defaults, returning it to what the lane itself states.

        The repo may arrive in the body or as a query parameter; either way an unknown one is a
        refusal rather than a silent no-op.
        """
        selected = _defaults_repo(body if isinstance(body, dict) else {}, repo)
        model_defaults.clear(settings_dir, selected.model_id)
        return _defaults_status(selected, _parameter_surface(selected))

    return app


def from_environment() -> FastAPI:
    data = Path(os.environ["STUDIOHUB_CHAT_DATA_DIR"])
    models = Path(os.environ["STUDIOHUB_MODELS_DIR"])
    manifests = Path(os.environ["STUDIOHUB_MODEL_MANIFESTS"])
    engine = ChatModelManager.from_catalog(manifests, models)
    release = json.loads(Path(os.environ["STUDIOHUB_RELEASE_MANIFEST"]).read_text())
    identity = release["components"]["chat"]["runtime_sha256"]
    if not isinstance(identity, str) or len(identity) != 64:
        raise ValueError("Release manifest lacks exact Chat runtime identity")
    policy = memory_policy_module.MemoryPolicy(data, engine)
    policy.start()
    return create_app(engine, policy, os.environ["STUDIOHUB_FLEET_TOKEN"], identity,
                      data_dir=data)
