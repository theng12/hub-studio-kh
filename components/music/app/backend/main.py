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

from . import catalog
from .generation import (DEFAULTS, MAX_DURATION_SECONDS, MIN_DURATION_SECONDS,
                         MODEL_REVISION, PACKAGE_ID, GenerationManager,
                         final_matches)


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

    @app.get("/api/health")
    def health() -> dict:
        active = [job for job in manager.list() if job["state"] in ("queued", "running")]
        files = manager.preflight()
        return {"ok": True, "app_version": "standalone", "generation": {
            "available": True, "busy": bool(active),
            "queued": sum(job["state"] == "queued" for job in active),
            "running": sum(job["state"] == "running" for job in active),
            "runtime_revision": manager.runtime_sha256,
            "checkpoints": files["state"],
            "engine_loaded": bool(getattr(manager.executor, "child_pid", None)),
            "engine_child_pid": getattr(manager.executor, "child_pid", None)}}

    @app.get("/api/catalog")
    def catalog_route() -> dict:
        state = readiness()
        files = state["files"]
        models = []
        for model in catalog.CATALOG:
            entry = catalog.serialize_model(model)
            cached = (files["state"] == "cached" and model.checkpoint == "acestep-v15-turbo")
            entry.update({
                "name": model.label,
                "model_id": model.repo,
                "modality": "music",
                "hub_operation": "music.text_to_music",
                "model_revision": MODEL_REVISION,
                "qualified_revision": MODEL_REVISION,
                "qualified_revision_match": True,
                "runtime_revision": manager.runtime_sha256,
                "runtime_compatible": state["runtime_identity"]["compatible"],
                "runtime_ready": state["runtime_identity"]["compatible"],
                "memory_eligible": state["host"]["eligible"],
                "min_total_memory_gb": model.min_unified_memory_gb,
                "qualified_minimum_memory_gb": QUALIFIED_MINIMUM_MEMORY_BYTES / 1e9,
                "memory_warning": state["host"].get("memory_warning"),
                "cache": {"state": "cached" if cached else "missing",
                          "package_id": PACKAGE_ID,
                          "model_revision": MODEL_REVISION,
                          "manifest_revision": files.get("manifest_revision"),
                          "checkpoints_root": files.get("checkpoints_root")},
                "readiness": {"ready": cached and state["host"]["eligible"],
                              "files": files, "host": state["host"],
                              "reason": None if cached else files.get("reason") or files["state"]},
                "execution_ready": cached and state["host"]["eligible"],
                "available": cached and state["host"]["eligible"],
                "generation_defaults": dict(DEFAULTS),
                "generation_limits": {
                    "min_duration_seconds": MIN_DURATION_SECONDS,
                    "max_duration_seconds": MAX_DURATION_SECONDS,
                    "single_pass_max_seconds": model.long_form.single_pass_max_seconds,
                    "min_steps": 1, "max_steps": 50,
                    "min_guidance": 0.0, "max_guidance": 30.0,
                    "min_seed": -1, "max_seed": 2 ** 32 - 1},
                "sample_rate_hz": model.sample_rate_hz,
                "channels": model.channels,
                "qualification": {
                    "historical_source_workload":
                        "30 s instrumental, 8 steps, turbo DiT + 0.6B LM, MLX backend, tier4",
                    "packaged_inference": "single standalone generation proof on this Mac"},
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

    # A stopped worker must not leave a 4-5 GB engine child holding the model.
    # Uvicorn runs shutdown handlers on SIGTERM/SIGINT before the process ends.
    app.router.add_event_handler("shutdown", manager.close)

    @app.get("/api/cache/{repo:path}")
    def cache_route(repo: str) -> dict:
        model = catalog.get_model(repo)
        status = cache_status()
        try:
            plan = manager.cache.download_plan(model)
        except (OSError, ValueError, KeyError) as exc:
            plan = {"error": str(exc)}
        if model is None:
            return {"repo": repo, "known": False, "cache": status,
                    "download_plan": plan}
        return {"repo": repo, "known": True, "package_id": PACKAGE_ID,
                "model": model.checkpoint, "cache": status,
                "model_cache": manager.cache.status_for_model(model),
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
