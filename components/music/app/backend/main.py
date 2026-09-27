"""Private standalone Music API for the pinned ACE-Step 1.5 turbo package.

Surface and conventions are copied from the Image and Voice workers: the same
owner credential on every route, the same ``{"job": ...}`` envelope, and the
same catalogue fields the Hub reads (``repo``, ``cache.model_revision``,
``min_unified_memory_gb``, ``capabilities``).  The Hub's broker posts the
modality envelope from ``MODALITY["music"]`` — ``POST /api/generate/txt2music``
with a ``prompt`` and ``repo`` — and reads the artifact from the job's
``output_url``.
"""
from __future__ import annotations

import hmac
import os
from pathlib import Path
import platform
import subprocess

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from . import catalog, memory_policy
from .generation import (GenerationManager, defaults_in_force, final_matches, lane_contract,
                         lane_parameters)

from components import app_version, model_defaults


# The 30 s probe ran on this 11.8 GB Mac at tier4, so 8 GB is a working floor
# rather than a guess.  The Hub's admission governor still owns the live
# dispatch decision; this only refuses a host that cannot run the engine at all.
QUALIFIED_MINIMUM_MEMORY_BYTES = 8_000_000_000


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
             "qualification": "single 30 s text2music generation on 16 GB Apple "
                              "Silicon (tier4, 480 s cap) from the ACE-Step probe"}
    if memory < QUALIFIED_MINIMUM_MEMORY_BYTES:
        state["memory_warning"] = (
            f"This Mac has {memory / 1024 ** 3:.0f} GB of unified memory. "
            "ACE-Step is qualified at 8 GB and above; below that the 2B DiT and "
            "0.6B planner may not both fit.")
    return state


def create_app(manager: GenerationManager, token: str) -> FastAPI:
    if not token:
        raise ValueError("Private Music worker requires its owning Hub credential")
    app = FastAPI(title="Studio Hub Music", version="1")
    # One resident ACE-Step engine child, released when the operator's policy says so.
    # Before this module existed the only things that stopped that child were a cancel and
    # the worker exiting, so its DiT and planner stayed in unified memory between jobs -
    # the residency a machine shared by several Studios cannot afford.
    memory_policy.start_background(manager)

    @app.middleware("http")
    async def require_owner(request: Request, call_next):
        offered = (request.headers.get("x-studio-token") or request.headers.get("x-hub-token")
                   or request.headers.get("authorization", "").removeprefix("Bearer "))
        if not hmac.compare_digest(offered, token):
            return JSONResponse({"detail": "Owner credential required"}, status_code=401)
        return await call_next(request)

    def readiness() -> dict:
        files = manager.preflight()
        host = _host_eligibility()
        ready = files["state"] == "cached" and host["eligible"]
        return {"ready": ready, "files": files, "host": host,
                "runtime_identity": {"id": manager.runtime_sha256,
                                     "compatible": files["state"] == "cached"}}

    def cache_status() -> dict:
        try:
            return manager.cache.status()
        except (OSError, ValueError, KeyError) as exc:
            return {"state": "manifest_unavailable", "reason": str(exc)}

    @app.get("/api/fleet/activity")
    def fleet_activity() -> dict:
        """The optional private activity contract the Hub polls after health."""
        return manager.activity_snapshot()

    @app.get("/api/health")
    def health() -> dict:
        active = [job for job in manager.list() if job["state"] in ("queued", "running")]
        files = manager.preflight()
        return {"ok": True, **app_version.version_fields(), "generation": {
            "available": True, "busy": bool(active),
            "queued": sum(job["state"] == "queued" for job in active),
            "running": sum(job["state"] == "running" for job in active),
            "runtime_revision": manager.runtime_sha256,
            "checkpoints": files["state"],
            "engine_loaded": bool(getattr(manager.executor, "child_pid", None)),
            "engine_child_pid": getattr(manager.executor, "child_pid", None)}}

    @app.get("/api/catalog")
    def catalog_route() -> dict:
        host = _host_eligibility()
        models = []
        for model in catalog.CATALOG:
            try:
                files = manager.preflight(model.repo)
            except (OSError, ValueError, KeyError) as exc:
                files = {"state": "manifest_unavailable", "reason": str(exc)}
            cached = files["state"] == "cached"
            ready = cached and host["eligible"]
            # Every published fact below is this model's own; nothing here falls
            # back to the first catalog entry, so a newly added model cannot be
            # advertised with ACE-Step's revision, caps or qualification.
            revision = model.revision
            # The values this worker will actually render with on this Mac: what the model
            # states, overridden per parameter by what the operator chose (`model_defaults.json`).
            # One call, so `generation_defaults` and the lane contract's per-parameter `default`
            # cannot publish two answers to one question — and neither can publish a number
            # `validate_request` would refuse, because `resolved()` validates on the way out too.
            # The app pre-fills its form from `generation_defaults` and sends every field, so this
            # is how a stored default reaches an app-dispatched job.
            defaults = {**model.defaults, **defaults_in_force(model, manager.data)}
            limits = dict(model.limits)
            qualification = dict(model.qualification)
            entry = catalog.serialize_model(model)
            entry.update({
                "name": model.label,
                "model_id": model.repo,
                "modality": "music",
                "hub_operation": "music.text_to_music",
                "model_revision": revision,
                "qualified_revision": revision,
                "qualified_revision_match": True,
                "runtime_revision": manager.runtime_sha256,
                "runtime_compatible": cached,
                "runtime_ready": cached,
                "memory_eligible": host["eligible"],
                "min_total_memory_gb": model.min_unified_memory_gb,
                "qualified_minimum_memory_gb": QUALIFIED_MINIMUM_MEMORY_BYTES / 1e9,
                "memory_warning": host.get("memory_warning"),
                # The package's own state, not a flattening of it. Every failure above already
                # named itself — `manifest_unavailable`, `engine_root_missing`, `invalid` — and
                # collapsing them all to "missing" told the operator to download a package that
                # was installed, hashed and verified, whenever the real problem was a manifest
                # the worker could not read.
                "cache": {"state": files["state"],
                          "reason": files.get("reason"),
                          "package_id": model.package_id,
                          "model_revision": revision,
                          "manifest_revision": files.get("manifest_revision"),
                          "checkpoints_root": files.get("checkpoints_root")},
                "readiness": {"ready": ready, "files": files, "host": host,
                              "reason": None if cached
                              else files.get("reason") or files["state"]},
                "execution_ready": ready,
                "available": ready,
                "generation_defaults": defaults,
                # The same two numbers in their other spelling on this row: the model's own
                # `inference_steps`/`guidance_scale` fields are the `steps`/`guidance_scale`
                # defaults, so they read the resolved map rather than disagreeing with the field
                # beside them. A model that publishes no such default keeps its own field.
                "inference_steps": defaults.get("steps", model.inference_steps),
                "guidance_scale": defaults.get("guidance_scale", model.guidance_scale),
                "generation_limits": limits,
                "sample_rate_hz": model.sample_rate_hz,
                "channels": model.channels,
                # How to ask for this lane's work, per model: the operation, the parameters this
                # model's own validator accepts and enforces, the artifact, the state vocabulary
                # and what a cancel does not promise. Built in `generation.py` beside the
                # validators, from this model's own limits, so it cannot drift from them, with
                # each settable parameter's `default` resolved to the value in force on this Mac.
                "lane_contract": lane_contract(model, manager.data),
                "qualification": qualification,
            })
            models.append(entry)
        return {"models": models,
                "families": [catalog.serialize_family(f) for f in catalog.families()]}

    @app.post("/api/generate/txt2music")
    def submit(body: dict) -> dict:
        state = readiness()
        if not state["host"]["eligible"]:
            raise HTTPException(409, state["host"]["reason"])
        try:
            return {"job": manager.submit(body)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/generate/jobs")
    def jobs() -> dict:
        return {"jobs": manager.list()}

    @app.get("/api/generate/jobs/{job_id}")
    def job(job_id: str) -> dict:
        value = manager.get(job_id)
        if value is None:
            raise HTTPException(404, "job not found")
        return {"job": value}

    def _cancel(job_id: str) -> dict:
        value = manager.cancel(job_id)
        if value is None:
            raise HTTPException(404, "job not found")
        return {"job": value}

    # The Hub's broker cancels with DELETE /api/generate/jobs/{id}; the explicit
    # POST is the documented alias the task asked for.  Both reach the same
    # manager call, so a client cannot get a different answer depending on verb.
    @app.delete("/api/generate/jobs/{job_id}")
    def cancel(job_id: str) -> dict:
        return _cancel(job_id)

    @app.post("/api/generate/jobs/{job_id}/cancel")
    def cancel_post(job_id: str) -> dict:
        return _cancel(job_id)

    @app.get("/api/generate/jobs/{job_id}/audio")
    def audio(job_id: str):
        value = manager.get(job_id)
        if value is None or value["state"] != "done":
            raise HTTPException(404, "final WAV is unavailable")
        path = manager.outputs / f"{job_id}.wav"
        if not final_matches(value, path):
            raise HTTPException(409, "final WAV changed after generation")
        return FileResponse(path, media_type="audio/wav",
                            filename=f"{job_id}.wav",
                            headers={"X-Content-SHA256": value["sha256"]})

    @app.get("/api/generate/jobs/{job_id}/metadata")
    def metadata(job_id: str):
        value = manager.get(job_id)
        if value is None or value["state"] != "done":
            raise HTTPException(404, "artifact sidecar is unavailable")
        path = manager.outputs / f"{job_id}.json"
        if path.is_symlink() or not path.is_file():
            raise HTTPException(404, "artifact sidecar is unavailable")
        return FileResponse(path, media_type="application/json")

    @app.post("/api/generate/jobs/{job_id}/ack")
    def ack(job_id: str) -> dict:
        receipt = manager.ack(job_id)
        if receipt is None:
            raise HTTPException(404, "completed Music job not found")
        return receipt

    @app.get("/api/cache")
    def cache_root() -> dict:
        return cache_status()

    # ── model memory policy ────────────────────────────────────────────────
    # The fleet contract every Studio publishes, matching the Voice worker's
    # `GET/PUT /api/memory-policy` and `POST /api/memory/release`. The Hub's
    # FleetMemoryControl reads all of them through this one shape, and renders a Studio
    # without it as "Needs an update".

    @app.get("/api/memory-policy")
    def get_memory_policy() -> dict:
        return memory_policy.status()

    @app.put("/api/memory-policy")
    def put_memory_policy(body: dict | None = None) -> dict:
        try:
            return memory_policy.save((body or {}).get("mode"))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/memory/release")
    def release_memory() -> dict:
        try:
            return memory_policy.release_now()
        except RuntimeError as exc:
            # 409, like Image and Subtitle: the work is active, not the request malformed.
            raise HTTPException(409, str(exc)) from exc

    # ── per-model generation defaults (`model_defaults`) ───────────────────
    # Which numbers the next job renders *with*: `duration_seconds`, `steps`, `guidance_scale`,
    # `seed` — plus the controls only a runtime that accepts them publishes (`shift`,
    # `instrumental`, `lyrics`, `vocal_language` for ACE-Step; none for Stable Audio 3) — stored
    # per model and per Mac, validated against the very bounds `lane_contract` publishes, and
    # applied by `validate_request` to a parameter the request **omits** (an explicitly supplied
    # parameter always wins).
    #
    # This is a different axis from `/api/memory-policy` above: that is *when a resident engine
    # gives its weights back*, this is *what the request asks the weights for*. Stored in a
    # different file (`model_defaults.json`, beside `memory_policy.json`), and neither setting
    # changes the other.
    #
    # A parameter the lane marks `required` (`prompt`) is not defaultable: it is per-request
    # content, and a stored value would render something nobody wrote this time.

    def model_defaults_status() -> dict:
        """Every registered model's published parameters and this Mac's values in force."""
        return model_defaults.status(
            manager.data,
            [(model.repo, lane_parameters(model)) for model in catalog.CATALOG])

    def registered_model(repo: object):
        """The catalog entry a route names by **repo id**, or a refusal that says which exist.

        Deliberately the repo id and not ``catalog.get_model``'s package-id alias: the stored
        defaults are keyed by repo, so accepting a package id here would write a row the catalog
        would never read — a default that silently does nothing.
        """
        model = next((item for item in catalog.CATALOG if item.repo == repo), None)
        if model is None:
            raise HTTPException(
                422, f"{repo!r} is not a registered music model; this worker serves: "
                     + ", ".join(item.repo for item in catalog.CATALOG))
        return model

    @app.get("/api/model-defaults")
    def get_model_defaults() -> dict:
        return model_defaults_status()

    @app.put("/api/model-defaults")
    def put_model_defaults(body: dict | None = None) -> dict:
        values = (body or {}).get("values")
        if not isinstance(values, dict):
            raise HTTPException(422, "values must be an object of parameter to default")
        model = registered_model((body or {}).get("repo"))
        try:
            model_defaults.save(manager.data, model.repo, values, lane_parameters(model))
        except ValueError as exc:
            # The shared reader, so all five Studios answer a bad PUT the same way: every refused
            # parameter with its own reason, or — if this is not one of our refusals — the message.
            raise HTTPException(422, detail=model_defaults.refusals(exc) or str(exc)) from exc
        return model_defaults_status()

    @app.delete("/api/model-defaults")
    def delete_model_defaults(body: dict | None = None, repo: str | None = None) -> dict:
        # Either a JSON body (the shape the app sends) or `?repo=`, so a caller that cannot put a
        # body on DELETE still reaches the same call.
        model = registered_model((body or {}).get("repo") or repo)
        model_defaults.clear(manager.data, model.repo)
        return model_defaults_status()

    # A stopped worker must not leave a 4-5 GB engine child holding the model.
    # Uvicorn runs shutdown handlers on SIGTERM/SIGINT before the process ends.
    app.router.add_event_handler("shutdown", manager.close)

    @app.get("/api/cache/{repo:path}")
    def cache_route(repo: str) -> dict:
        model = catalog.get_model(repo)
        status = cache_status()
        if model is None:
            return {"repo": repo, "known": False, "cache": status,
                    "download_plan": manager.cache.download_plan(None)}
        # One model's package view, never another's: the same lookup preflight uses.
        try:
            view = manager.cache_for(model.repo)
            plan = view.download_plan(model)
            model_cache = view.status_for_model(model)
        except (OSError, ValueError, KeyError) as exc:
            plan = {"error": str(exc)}
            model_cache = {"repo": model.repo, "checkpoint": model.checkpoint,
                           "state": "manifest_unavailable",
                           "checkpoints": [{"name": item.name, "state": "manifest_unavailable"}
                                           for item in model.checkpoints]}
        return {"repo": repo, "known": True, "package_id": model.package_id,
                "model": model.checkpoint, "cache": status,
                "model_cache": model_cache,
                "download_plan": plan}

    return app


def from_environment() -> FastAPI:
    data = Path(os.environ["STUDIOHUB_MUSIC_DATA_DIR"])
    outputs = os.environ.get("STUDIOHUB_MUSIC_OUTPUT_DIR")
    manager = GenerationManager(
        data,
        outputs=Path(outputs) if outputs else None,
        engine_root=os.environ.get("STUDIOHUB_MUSIC_ENGINE_ROOT"),
        manifest_path=os.environ.get("STUDIOHUB_MUSIC_MANIFEST"),
        library_dir=os.environ.get("STUDIOHUB_MUSIC_LIBRARY_DIR"),
        runtime_sha256=os.environ.get("STUDIOHUB_MUSIC_RUNTIME_SHA256"),
        machine=os.environ.get("STUDIOHUB_MUSIC_MACHINE", "local"),
    )
    return create_app(manager, os.environ["STUDIOHUB_FLEET_TOKEN"])
