"""The packaged, private Studio Hub API surface.

This module intentionally exposes the durable job, transcription and settings
contracts. Legacy Pinokio control, updater and fleet-repair routes are not
registered by the packaged application.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from . import output_storage, standalone_config
from .backend import auth, broker, cloud_guard, control_plane, ledger, model_ids, routing as routing_rule
from .backend import app_release, shared_voices, transcription_jobs
from .backend.monitor import StudioMonitor


def _model_library() -> Path:
    value = os.environ.get("STUDIOHUB_MODELS_DIR", "").strip()
    if not value:
        raise ValueError("STUDIOHUB_MODELS_DIR is required for model management")
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("selected model library is unavailable")
    return root


def _model_catalog() -> dict[str, dict[str, Any]]:
    location = os.environ.get("STUDIOHUB_MODEL_MANIFESTS", "").strip()
    root = Path(location).expanduser().resolve() if location else Path(__file__).resolve().parents[2] / "model-manifests"
    try:
        manifests = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(root.glob("*.json"))]
    except OSError as exc:
        raise ValueError("model manifest catalog is unavailable") from exc
    from components import model_packages
    for manifest in manifests:
        model_packages.validate_manifest(manifest)
    return {manifest["package_id"]: manifest for manifest in manifests}


def _dependency_package_ids(catalog: dict[str, dict[str, Any]]) -> set[str]:
    """Package ids that exist only because another package needs them.

    A codec, a tokenizer and a real model are indistinguishable in this row otherwise: all of them
    report `engine=voice-mlx`, `state=files_verified` and `runtime_state=ready`, which is how a
    tokenizer ended up offered in a voice picker on the gateway's side. The catalogue already says
    which packages are dependencies - each manifest lists its companions in `dependencies` - so the
    fact is stated here rather than left to be inferred from membership of some other list.
    """
    required: set[str] = set()
    for entry in catalog.values():
        for dependency in entry.get("dependencies") or ():
            required.add(str(dependency))
    return required


def _worker_for_engine(engine: str) -> str:
    """Which worker runs a manifest's engine.

    Kept as one named mapping rather than an inline dict because it has to agree with the engine
    allowlist in `components/model_packages.py`, and for a while it did not: `chat-mlx` was added
    to the allowlist and to the shipped catalog, but not here, so `/api/hub/model-packages` raised
    `KeyError: 'chat-mlx'` and answered 500. The app reads that route with a hard `try` inside its
    refresh, so the 500 did not degrade one list - it marked the whole Hub unreachable and left
    every model picker empty. `tests/test_model_manifest_catalog.py` now fails if the two lists
    drift apart again.
    """
    mapping = {"image-mflux": "image", "voice-mlx": "voice", "voice-vits": "voice",
               "subtitle-mlx": "subtitle", "music-mlx": "music", "chat-mlx": "chat"}
    try:
        return mapping[engine]
    except KeyError as exc:
        raise ValueError(
            f"the manifest engine {engine!r} has no worker; add it here and to the engine "
            f"allowlist in components/model_packages.py") from exc


def _model_state(manifest: dict[str, Any], catalog: dict[str, dict[str, Any]], monitor: StudioMonitor) -> dict[str, Any]:
    from components import model_packages
    receipt = model_packages.preflight(manifest, _model_library(), catalog=catalog)
    worker_id = _worker_for_engine(manifest["engine"])
    worker = next((item for item in monitor.registry if item["id"] == worker_id), None)
    # A paused model still reports verified files and a compatible runtime; it is the Hub's own
    # circuit breaker that refuses it. Without this the app showed every gate open and the job was
    # then declined before dispatch, which read as the model silently not working.
    paused = broker.model_block_state(worker_id, manifest["model_id"])
    return {
        **receipt,
        **standalone_config.model_readiness({
            "files_state": receipt["state"],
            "minimum_macos": manifest["minimum_macos"],
            "runtime_identity": (worker or {}).get("runtime_identity", {"compatible": False}),
        }),
        "model_id": manifest["model_id"],
        "revision": manifest["revision"],
        "engine": manifest["engine"],
        # `model` for something a person can pick; `dependency` for a package that only exists to
        # serve another one. Consumers must not have to infer this.
        "type": ("dependency" if manifest["package_id"] in _dependency_package_ids(catalog) else "model"),
        "dependencies": list(manifest.get("dependencies") or ()),
        "worker_id": worker_id,
        "worker_state": "running" if worker else "unavailable",
        "paused": paused is not None,
        "paused_reason": (paused or {}).get("reason"),
        "paused_studio": (paused or {}).get("studio"),
        "paused_failures": (paused or {}).get("failures"),
    }


def _summary(batch: dict[str, Any]) -> dict[str, Any]:
    return {**broker.batch_summary(batch), "items": [
        broker.public_item(batch, item) for item in batch["items"]
    ]}


def _app_version() -> str:
    try:
        return str(json.loads(Path(os.environ["STUDIOHUB_RELEASE_MANIFEST"]).read_text())["version"])
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return "unknown"


def _app_build() -> str:
    """This build's exact commit, so two copies of one product version can be told apart."""
    try:
        value = json.loads(Path(os.environ["STUDIOHUB_RELEASE_MANIFEST"]).read_text())["implementation_commit"]
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return "unknown"
    return str(value)[:7]


def machine_health(states: list[tuple[str, str]]) -> tuple[str, str | None]:
    """One Mac's health from all of its Studios, not from whichever one is listed first.

    `fleet_overview` took the first studio on a machine and reported that studio's status as the
    machine's health, so a Mac running Voice and Image with Image down was reported as "up" if Voice
    happened to come first in the registry - and as "down" while Voice was working fine if it did not.
    A person reading the Fleet page is asking about the Mac.

    Returns the health and, when something is wrong, a sentence naming what.
    """
    up = [name for name, status in states if status == "up"]
    down = [name for name, status in states if status not in ("up", "unknown")]
    if down and not up:
        health = "down"
    elif down:
        health = "degraded"
    elif up:
        health = "up"
    else:
        health = "unknown"
    detail = None
    if down:
        listed = ", ".join(sorted(down))
        detail = f"{listed} is not answering" if len(down) == 1 else f"{listed} are not answering"
    return health, detail


def _submission_readiness(envelope: dict[str, Any], monitor: StudioMonitor) -> dict[str, Any] | None:
    """Return an explicit refusal when this packaged model cannot run here.

    The catalog is the trusted boundary for shipped model packages.  Keeping
    this check at submit time prevents a job from entering the durable queue
    only to wait forever for absent files, a mismatched runtime, or an OS gate.
    """
    # Preserve the Broker's established input-validation response for malformed
    # envelopes; readiness applies only once a model was actually requested.
    if not envelope.get("model"):
        return None
    catalog = _model_catalog()
    manifest = next((row for row in catalog.values()
                     if row["model_id"] == envelope.get("model")), None)
    if manifest is None:
        return {"code": "MODEL_NOT_PACKAGED", "detail": "model is not in the packaged catalog"}
    expected_modality = {
        "image-mflux": "image", "voice-mlx": "voice", "voice-vits": "voice",
        "subtitle-mlx": "transcription", "music-mlx": "music", "chat-mlx": "chat",
    }.get(manifest["engine"])
    if expected_modality != envelope.get("modality"):
        return {"code": "MODEL_MODALITY_MISMATCH", "detail": "model is not available for this job modality"}
    state = _model_state(manifest, catalog, monitor)
    if state["dispatch_state"] != "eligible":
        return {"code": "MODEL_NOT_READY", "detail": "model files, runtime, OS, or chip are not ready",
                "readiness": state}
    # A Studio that is not running cannot be given work. Without this the job was accepted - the
    # gates below only refuse for storage, and the readiness gate above only covers files, runtime,
    # macOS and chip - and then queued for ever, because dispatch needs a live studio and nothing
    # ever said why none came. A worker that dies is never removed from `monitor.registry` (there is
    # no deregistration), so liveness is the monitor's own status for it; "unknown" means it has not
    # been checked yet, which must not refuse a job on a Hub that has just started.
    capable = [worker for worker in monitor.registry
               if worker.get("modality") == expected_modality]
    if not capable:
        return {"code": "STUDIO_NOT_RUNNING",
                "detail": f"no {expected_modality} Studio is running on this Mac",
                "readiness": state}
    statuses = [monitor.status.get(worker.get("id"), {}).get("status") for worker in capable]
    if all(status not in (None, "unknown", "up") for status in statuses):
        return {"code": "STUDIO_NOT_RUNNING",
                "detail": f"the {expected_modality} Studio is not answering; restart the local services",
                "readiness": state}

    settings = standalone_config.load_device_settings()["effective"]
    workers = [worker for worker in capable if worker.get("local") is True]
    blocked = [output_storage.admission(machine=worker.get("machine", "local"), max_bytes=settings["max_bytes"],
               retention_days=settings["retention_days"], enabled=settings["cleanup_enabled"],
               minimum_free_bytes=settings["minimum_free_bytes"],
               managed_output_roots=worker.get("managed_output_roots"))
               for worker in workers]
    blocked = [refusal for refusal in blocked if refusal]
    if workers and len(blocked) == len(workers):
        return blocked[0]
    return None


# ── node-owned admission for fleet work ────────────────────────────────────
# A controller dispatches by posting straight at this Mac's worker through the
# peer proxy. The node does not own that durable batch, so this proxy is the
# only place it can enforce its own operator pause, model protection, output
# policy and physical heavy-work lease decisions - and it cannot queue an
# opaque peer job, so the only way to stop a second submission overlapping on
# the same Mac is to hold a machine lease until the accepted job is observed
# terminal. The controller's cached capability view is never trusted for this.
_PEER_LEASE_TTL_S = 6 * 3600.0
_PEER_LEASE_LOCK = threading.Lock()
_PEER_LEASES: dict[str, tuple[str, float]] = {}   # owner -> (machine, monotonic expiry)
_PEER_JOBS: dict[tuple[str, str], str] = {}       # (worker_id, job_id) -> owner
_PEER_TERMINAL_STATES = frozenset({
    "done", "error", "failed", "cancelled", "canceled", "expired",
})
_PEER_JOB_PATH = re.compile(r"^/api/(?:generate/jobs|generate/transcribe/jobs)/([^/]+)")


def _release_peer_lease(owner: str) -> None:
    with _PEER_LEASE_LOCK:
        row = _PEER_LEASES.pop(owner, None)
        for key, value in list(_PEER_JOBS.items()):
            if value == owner:
                _PEER_JOBS.pop(key, None)
    if row is not None:
        broker.release_external_machine(row[0], owner)


def _prune_peer_leases() -> None:
    """Drop peer leases whose job was never observed terminal and whose safety
    window elapsed, so a vanished controller cannot reserve a Mac for ever."""
    now = time.monotonic()
    with _PEER_LEASE_LOCK:
        expired = [owner for owner, (_machine, expiry) in _PEER_LEASES.items()
                   if expiry <= now]
    for owner in expired:
        _release_peer_lease(owner)


async def _peer_lease_loop() -> None:
    """Expire abandoned peer leases even when no further peer request arrives.

    Without this, a controller that vanished mid-job would leave this Mac's own
    scheduler treating the machine as forever busy.
    """
    import asyncio

    try:
        while True:
            await asyncio.sleep(60.0)
            _prune_peer_leases()
    except asyncio.CancelledError:
        return


def _reserve_peer_machine(worker_id: str, machine: str) -> str | None:
    owner = f"peer:{worker_id}:{uuid.uuid4().hex}"
    if not broker.acquire_external_machine(machine, owner):
        return None
    with _PEER_LEASE_LOCK:
        _PEER_LEASES[owner] = (machine, time.monotonic() + _PEER_LEASE_TTL_S)
    return owner


def _worker_job(payload: object) -> tuple[str, str] | None:
    if not isinstance(payload, dict):
        return None
    job = payload.get("job")
    if not isinstance(job, dict) or not isinstance(job.get("id"), str) or not job["id"]:
        return None
    return job["id"], str(job.get("state") or "")


def _settle_peer_lease(owner: str, worker_id: str, response: Response) -> None:
    """Keep the lease for an accepted asynchronous job; free it otherwise.

    A synchronous route (Subtitle's ``/api/transcribe``) or a refusal is already
    finished when the response arrives, so its lease must not outlive the call.
    """
    if response.status_code >= 400:
        _release_peer_lease(owner)
        return
    payload = None
    if "json" in response.headers.get("content-type", ""):
        try:
            payload = json.loads(response.content)
        except (ValueError, TypeError):
            payload = None
    job = _worker_job(payload)
    if job is None or job[1] in _PEER_TERMINAL_STATES:
        _release_peer_lease(owner)
        return
    with _PEER_LEASE_LOCK:
        row = _PEER_LEASES.get(owner)
        if row is not None:
            _PEER_LEASES[owner] = (row[0], time.monotonic() + _PEER_LEASE_TTL_S)
        _PEER_JOBS[(worker_id, job[0])] = owner


def _observe_peer_response(worker_id: str, tail: str, response: Response) -> None:
    """Free a held machine once a proxied response shows its job terminal."""
    match = _PEER_JOB_PATH.match(tail)
    if match is None:
        return
    with _PEER_LEASE_LOCK:
        owner = _PEER_JOBS.get((worker_id, match.group(1)))
    if owner is None:
        return
    terminal = False
    if "json" in response.headers.get("content-type", ""):
        try:
            job = _worker_job(json.loads(response.content))
        except (ValueError, TypeError):
            job = None
        terminal = job is not None and job[0] == match.group(1) and job[1] in _PEER_TERMINAL_STATES
    elif response.status_code < 400 and response.content:
        # A raw artifact is served only for a job that has finished.
        terminal = True
    if terminal:
        _release_peer_lease(owner)


def _peer_work_request(method: str, path: str) -> bool:
    """Whether this proxied request asks the node to create new work.

    Status, cancellation, artifact and acknowledgement paths are deliberately
    excluded: an operator pausing a mode must not lock anyone out of work this
    Mac already accepted.
    """
    if method != "POST":
        return False
    cleaned = path.strip("/")
    return (cleaned.startswith("api/generate/")
            or cleaned == "api/transcribe"
            or cleaned == "v1/chat/completions")


async def _request_model(request: Request, body: bytes, content_type: str) -> str:
    """The model a work request names, or "" when it names none.

    Generation submits carry it as ``repo`` in a JSON body; Subtitle carries it
    as a multipart form field. A missing model only skips the model-specific
    gates - the worker still validates the request it is sent.
    """
    if not body:
        return ""
    kind = (content_type or "").split(";", 1)[0].strip().lower()
    if kind == "application/json":
        try:
            payload = json.loads(body)
        except ValueError:
            return ""
        if isinstance(payload, dict):
            return str(payload.get("repo") or payload.get("model") or "").strip()
        return ""
    if kind in {"multipart/form-data", "application/x-www-form-urlencoded"}:
        try:
            form = await request.form()
        except Exception:
            return ""
        return str(form.get("model") or form.get("repo") or "").strip()
    return ""


def _peer_admission_refusal(worker: dict[str, Any], monitor: StudioMonitor,
                            model: str) -> dict[str, Any] | None:
    """This Mac's own answer, applied before its worker sees any peer request."""
    from .backend import registry

    machine = str(worker.get("machine") or "local")
    studio_id = str(worker.get("id") or "")
    if not registry.machine_enabled(machine):
        return {"code": "MACHINE_DISABLED", "detail": f"{machine} is disabled on this Mac"}
    if not registry.studio_enabled(machine, studio_id):
        return {"code": "STUDIO_DISABLED", "detail": f"{studio_id} is disabled on this Mac"}
    if model:
        paused = broker.model_block_state(studio_id, model)
        if paused is not None:
            return {"code": "MODEL_PAUSED",
                    "detail": str(paused.get("reason") or "model is paused on this Mac"),
                    "readiness": paused}
    if machine in broker.busy_machines():
        return {"code": "MACHINE_BUSY",
                "detail": f"{machine} is reserved for other work on this Mac"}
    settings = standalone_config.load_device_settings()["effective"]
    blocked = output_storage.admission(
        machine=machine, max_bytes=settings["max_bytes"],
        retention_days=settings["retention_days"], enabled=settings["cleanup_enabled"],
        minimum_free_bytes=settings["minimum_free_bytes"],
        managed_output_roots=worker.get("managed_output_roots"))
    if blocked:
        return blocked
    # The packaged model gate only applies where this Hub actually owns a model
    # library to verify against; without one the worker remains the authority on
    # whether its own model can run.
    if model and os.environ.get("STUDIOHUB_MODELS_DIR", "").strip():
        try:
            return _submission_readiness(
                {"model": model, "modality": worker.get("modality")}, monitor)
        except (ValueError, OSError, KeyError, TypeError):
            return {"code": "MODEL_CATALOG_UNAVAILABLE",
                    "detail": "model readiness could not be verified on this Mac"}
    return None


def _peer_refusal_response(refusal: dict[str, Any]) -> JSONResponse:
    code = str(refusal.get("code") or "NODE_REFUSED")
    status = 409
    if code == "OUTPUT_STORAGE_BLOCKED":
        status = 507
    elif code in {"MODEL_NOT_READY", "STUDIO_NOT_RUNNING", "MODEL_CATALOG_UNAVAILABLE"}:
        status = 503
    body: dict[str, Any] = {
        "detail": str(refusal.get("detail") or "this Mac refused the work"),
        "code": code,
    }
    if isinstance(refusal.get("readiness"), dict):
        body["readiness"] = refusal["readiness"]
    return JSONResponse(body, status_code=status)


def create_app(*, start_background: bool = True) -> FastAPI:
    """Create one owner-authenticated Hub bound to packaged worker descriptors."""
    monitor = StudioMonitor()
    broker.configure_monitor(monitor)
    broker.configure_admission_guard(lambda batch: _submission_readiness(batch, monitor))
    cloud_guard.configure_monitor(monitor)
    token = auth.load_token()
    # A Hub owns its fleet credential from the moment it runs, so an operator can read it
    # without having to provoke a request first. It is a separate scope from the Hub token on
    # purpose: this is what a controller is given.
    from .backend import peers as _peers
    _peers.fleet_token()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ledger.prepare_database()
        output_storage.prepare()
        output_storage.recover_process_leases()
        broker.restore_batches()
        transcription_jobs.restore_batches()
        # A pause this build can never clear is not a safety feature, it is a permanent refusal.
        try:
            manifests = _model_catalog().values()
            known = {str(manifest.get("model_id") or "") for manifest in manifests}
            known |= {alias for manifest in manifests for alias in (manifest.get("aliases") or [])}
            forgotten = broker.prune_model_protection({name for name in known if name})
            if forgotten:
                print(f"[hub] forgot pauses for models this build does not ship: "
                      f"{', '.join(forgotten)}", flush=True)
        except ValueError:
            pass
        storage_sweep = None
        peer_lease_sweep = None
        if start_background:
            # 50 GB or 30 days, whichever comes first, enforced for every studio - not only in
            # development. See job_storage.budget_loop.
            import asyncio as _asyncio
            from .backend import job_storage
            storage_sweep = _asyncio.create_task(
                job_storage.budget_loop(standalone_config.cleanup_interval_seconds()))
            peer_lease_sweep = _asyncio.create_task(_peer_lease_loop())
            output_storage.start_background(
                lambda: {worker.get("machine", "local") for worker in monitor.registry},
                standalone_config.load_device_settings,
            )
            monitor.start()
            # Mint the fleet credential at startup, rather than lazily on the first request that
            # presents a foreign token. Connections offers this value to a Mac that joins, and a
            # lazily minted one does not exist yet when the app reads the file - so the operator
            # saw "Unavailable" on the one screen that has to hand it out.
            from .backend import peers as peer_credentials
            peer_credentials.fleet_token()
            broker.start_dispatcher()
            transcription_jobs.start_dispatcher(monitor)
            # A shared voice has to survive a node being off, so the reconciler is what retries it.
            # The module was complete and nothing started it, which is the same shape of miss as a
            # route with no caller: the machinery existed and no voice could ever move.
            shared_voices.start_reconciler(monitor)
            await control_plane.runtime.start(monitor, _app_version())
        try:
            yield
        finally:
            if start_background:
                if storage_sweep is not None:
                    storage_sweep.cancel()
                    try:
                        await storage_sweep
                    except Exception:
                        pass
                if peer_lease_sweep is not None:
                    peer_lease_sweep.cancel()
                    try:
                        await peer_lease_sweep
                    except Exception:
                        pass
                await shared_voices.stop()
                await broker.stop_dispatcher()
                await transcription_jobs.stop()
                await control_plane.runtime.stop()
                await monitor.stop()
                from . import model_downloads
                model_downloads.shutdown()

    app = FastAPI(title="Studio Hub", version=_app_version(), lifespan=lifespan)
    app.middleware("http")(auth.make_middleware(token))
    app.state.monitor = monitor

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "workers": len(monitor.registry), "role": control_plane.load_settings()["role"]}

    @app.get("/api/version")
    def version() -> dict[str, str]:
        return {"version": _app_version(), "build": _app_build(),
                "source": "studiohub-mac@06e893e1643bffbcbd162fae122e5a4d0f29e283"}

    @app.get("/api/hub/settings")
    def settings() -> dict[str, dict[str, int | bool]]:
        return standalone_config.load_device_settings()

    @app.put("/api/hub/settings")
    def save_settings(body: dict[str, Any]) -> dict[str, dict[str, int | bool]]:
        try:
            value = standalone_config.preview_device_settings(**body)
            return standalone_config.save_device_settings(
                max_bytes=value["max_bytes"], retention_days=value["retention_days"],
                cleanup_enabled=value["cleanup_enabled"],
                cleanup_interval_seconds=value["cleanup_interval_seconds"],
                minimum_free_bytes=value["minimum_free_bytes"],
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/hub/settings/preview")
    def preview_settings(body: dict[str, Any]) -> dict[str, Any]:
        try:
            value = standalone_config.preview_device_settings(**body)
            roots_by_machine: dict[str, set[str]] = {}
            for worker in monitor.registry:
                if worker.get("local") is not True:
                    continue
                machine = str(worker.get("machine") or "local")
                roots = roots_by_machine.setdefault(machine, set())
                roots.update(root for root in worker.get("managed_output_roots", [])
                             if isinstance(root, str) and root)
            return {"effective": value, "machines": [output_storage.preview(
                machine=machine, max_bytes=value["max_bytes"], retention_days=value["retention_days"],
                enabled=value["cleanup_enabled"],
                minimum_free_bytes=value["minimum_free_bytes"],
                managed_output_roots=sorted(roots))
                for machine, roots in roots_by_machine.items()]}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/hub/settings/restore-defaults")
    def restore_settings() -> dict[str, dict[str, int]]:
        return standalone_config.restore_device_defaults()

    @app.get("/api/hub/resources")
    def resources(local_only: bool = True) -> dict[str, Any]:
        """This Mac's host and studio snapshot, as a peer Hub reads it.

        A controller will not send work to a machine it cannot see. Its `peers.dispatch_ready`
        needs a fresh snapshot from this endpoint, so without it a joined node is listed, is
        reachable, passes every other check - and is never dispatched to, with nothing said
        about why. `local_only=true` keeps this Mac from fanning the question back out to its
        own peers.
        """
        from .backend import resources as resource_stats
        try:
            host = resource_stats.host_stats()
        except Exception:
            host = {"error": "host statistics are unavailable"}
        studios: dict[str, Any] = {}
        for worker in monitor.registry:
            if local_only and worker.get("local") is not True:
                continue
            try:
                process = resource_stats.studio_process_stats(worker["port"])
            except Exception:
                process = None
            state = monitor.status.get(worker["id"]) or {}
            studios[worker["modality"]] = {
                "id": worker["id"],
                "machine": worker.get("machine", "local"),
                "status": state.get("status", "unknown"),
                "process": process,
            }
        try:
            proxy = resource_stats.proxy_stats()
        except Exception:
            proxy = None
        # The version travels with the host snapshot because it is what tells an operator
        # whether a node is running the build they think it is, and a snapshot is the only
        # thing a controller has to go on for a Mac it does not own.
        return {"host": host, "studios": studios, "proxy": proxy, "version": _app_version()}

    @app.get("/api/hub/workers")
    def workers() -> dict[str, Any]:
        # Token and process metadata are intentionally excluded from responses.
        fields = ("id", "title", "modality", "machine", "runtime_identity")
        return {"workers": [{key: worker[key] for key in fields if key in worker}
                            for worker in monitor.registry]}

    @app.get("/api/hub/controller")
    def controller() -> dict[str, Any]:
        return {"settings": control_plane.public_settings(),
                "runtime": control_plane.runtime.status(),
                "readiness": control_plane.runtime.readiness(),
                "capacity": control_plane.runtime.capacity(monitor)}

    @app.put("/api/hub/controller")
    def save_controller(body: dict[str, Any]) -> dict[str, Any]:
        if body.get("database_mode") == "shadow":
            raise HTTPException(409, "PostgreSQL shadow mode is unavailable in the packaged runtime")
        try:
            return control_plane.save_settings(body, clear_database_url=bool(body.get("clear_database_url")))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/hub/capabilities")
    async def capabilities() -> dict[str, Any]:
        from .backend import capabilities as capability_contract
        return await capability_contract.build_snapshot(
            monitor, app_version=_app_version(), settings=control_plane.public_settings(),
            readiness=control_plane.runtime.readiness(),
            base_capacity=control_plane.runtime.capacity(monitor),
        )

    @app.get("/api/hub/model-packages")
    def model_packages_list() -> dict[str, Any]:
        try:
            catalog = _model_catalog()
            return {"models": [_model_state(manifest, catalog, monitor)
                               for manifest in catalog.values()]}
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/api/hub/catalog")
    @app.get("/api/hub/models")
    async def catalog() -> dict[str, Any]:
        """Existing Hub aggregate contract, gathered only through private workers."""
        # model_id is the manifest's stable identifier, added to every model row
        # so /api/hub/models and /api/hub/jobs share one join key. The worker's
        # own repo key is preserved unchanged.
        return model_ids.annotate(await monitor.aggregate_catalog())

    @app.get("/api/hub/model-health")
    def model_health() -> dict[str, Any]:
        """Models the Hub has paused, and why. A pause is Hub state, not worker state, so it is
        reported here rather than inferred from a catalogue that still calls the model ready.

        ``models`` is the additive per-(machine, model) health answer: one row
        with a closed-vocabulary ``state`` and an honest ``reason``/``hint``.
        The existing ``paused`` key is kept unchanged.
        """
        from .backend import model_health as model_health_contract
        return {
            "paused": broker.model_protection_snapshot(),
            "models": model_health_contract.snapshot(monitor),
        }

    @app.post("/api/hub/model-health/recheck")
    async def model_health_recheck(body: dict[str, Any]) -> dict[str, Any]:
        """Let a repaired model back into service without restarting the Hub.

        Pausing is automatic after repeated terminal failures; until now *unpausing* was only
        possible by editing the Hub's own state file and relaunching the app.
        """
        model = str(body.get("model") or "").strip()
        if not model:
            raise HTTPException(400, "model is required")
        studio = str(body.get("studio") or "").strip()
        if not studio:
            matching = [row["studio"] for row in broker.model_protection_snapshot()
                        if row.get("model") == model]
            if len(matching) != 1:
                raise HTTPException(409, "Name the paused Studio for this model")
            studio = matching[0]
        try:
            return await broker.recheck_model_protection(studio, model)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    async def worker_read(worker_id: str, path: str) -> dict[str, Any]:
        """Authenticated pass-through read from one named worker.

        Public Subtitle job state must come from the Subtitle worker, never from the Voice
        worker, otherwise the studio separation is only nominal.
        """
        worker = next((row for row in monitor.registry if row["id"] == worker_id), None)
        label = worker_id.title()
        if worker is None:
            raise HTTPException(503, f"{label} worker is unavailable")
        try:
            response = await monitor._client.get(
                worker["base_url"] + path,
                headers={"X-Hub-Token": worker["hub_token"]}, timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, f"{label} worker read failed") from exc
        if not isinstance(payload, dict):
            raise HTTPException(502, f"{label} worker returned an invalid response")
        return payload

    async def voice_read(path: str) -> dict[str, Any]:
        return await worker_read("voice", path)

    @app.get("/studio/voice/api/generate/availability")
    async def voice_availability() -> dict[str, Any]:
        return await voice_read("/api/generate/availability")

    @app.get("/studio/voice/api/catalog")
    async def voice_catalog() -> dict[str, Any]:
        return await voice_read("/api/catalog")

    @app.get("/studio/voice/api/transcribe/availability")
    async def transcription_availability() -> dict[str, Any]:
        return await voice_read("/api/transcribe/availability")

    @app.get("/studio/voice/api/downloads")
    async def voice_downloads() -> dict[str, Any]:
        return await voice_read("/api/downloads")

    @app.get("/studio/voice/api/settings")
    async def voice_settings() -> dict[str, Any]:
        return await voice_read("/api/settings")

    # Independent Subtitle studio reads. These come from the subtitle worker, not Voice.
    @app.get("/studio/subtitle/api/catalog")
    async def subtitle_catalog() -> dict[str, Any]:
        return await worker_read("subtitle", "/api/catalog")

    @app.get("/studio/subtitle/api/transcribe/availability")
    async def subtitle_availability() -> dict[str, Any]:
        return await worker_read("subtitle", "/api/transcribe/availability")

    @app.get("/studio/subtitle/api/generate/transcribe/jobs")
    async def subtitle_jobs() -> dict[str, Any]:
        return await worker_read("subtitle", "/api/generate/transcribe/jobs")

    @app.get("/studio/subtitle/api/generate/transcribe/jobs/{job_id}")
    async def subtitle_job(job_id: str) -> dict[str, Any]:
        return await worker_read("subtitle", f"/api/generate/transcribe/jobs/{job_id}")

    # ── joined nodes ───────────────────────────────────────────────────────
    # One app is the controller and the others join it by address. Every route is behind the
    # Hub credential like the rest of the private surface: a join is not a public endpoint.

    @app.get("/api/hub/fleet/overview")
    async def fleet_overview() -> dict[str, Any]:
        """One row per machine in the fleet: is it there, what is it running, how fast is it.

        The pieces existed in three places - the peer snapshot for remote host state, the fleet
        roster for joined nodes, and the ledger for measured speed - and none of them answered
        "how is my fleet doing". This puts them in one row per Mac so the app can show it.

        The rows come from the *group*, not from whichever Macs happen to be in the dispatch
        registry right now. That distinction is the whole answer to "show me my pool": a Mac that
        has joined belongs in the list whether it is awake or asleep, and on a node the group
        includes the controller and the Macs beside it - which this Mac can only know by reading
        the roster back from the controller it joined.
        """
        from .backend import fleet_nodes as fleet
        from .backend import ledger, peers
        from .backend import resources as resource_stats
        measured = ledger.stats().get("by_machine") or {}
        local_role = str(control_plane.load_settings().get("role") or "standalone")
        pool = await fleet.pool()
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()

        # Members of the group as this Mac knows it. A roster row is probed before it is reported,
        # so `reachable` is a fact about now and `detail` says why not when it is not.
        members: dict[str, dict[str, Any]] = {}
        for entry in ([pool["controller"]] if pool.get("controller") else []) + pool["nodes"]:
            machine = str(entry.get("machine") or "")
            if machine and machine not in members:
                members[machine] = entry

        for studio in monitor.registry:
            machine = str(studio.get("machine") or "local")
            if machine in seen:
                continue
            seen.add(machine)
            on_this_machine = [item for item in monitor.registry
                               if str(item.get("machine") or "local") == machine]
            member = members.get(machine)
            if peers.is_local_studio(studio):
                try:
                    host = resource_stats.host_stats()
                except Exception:
                    host = {}
                health, detail = machine_health([
                    (str(item.get("title") or item.get("modality") or item["id"]),
                     (monitor.status.get(item["id"]) or {}).get("status", "unknown"))
                    for item in on_this_machine
                ])
                row = {
                    "machine": machine,
                    "label": "This Mac",
                    "role": local_role,
                    "reachable": True,
                    "version": _app_version(),
                    "health": health,
                    "detail": detail,
                    "studios": {"total": len(on_this_machine),
                                "up": sum((monitor.status.get(item["id"]) or {}).get("status") == "up"
                                          for item in on_this_machine)},
                }
            else:
                snapshot = peers.cached(machine) or {}
                # The roster's probe is the one read of this machine: it answers directly when no
                # current snapshot exists and, when there is one, reuses the peer poll the dispatch
                # path already trusts - so the page and the scheduler cannot disagree about a Mac.
                # Its host facts are what this row shows; a roster member is not read a second time
                # through the peer cache, which is what the separate `pool()` probe used to leave
                # unpopulated until the next poll happened to run.
                if member is not None:
                    reachable = member.get("state") == "reachable"
                    health = "up" if reachable else str(member.get("state") or "unknown")
                    detail = member.get("detail")
                    version = member.get("version")
                    host = member.get("host") or {}
                else:
                    reachable = bool(snapshot.get("reachable") and snapshot.get("auth"))
                    health = ("up" if snapshot.get("status") == "connected"
                              else str(snapshot.get("status") or "unknown"))
                    detail = None if snapshot else "this Mac has not reported in yet"
                    version = snapshot.get("version")
                    host = snapshot.get("host") or {}
                row = {
                    "machine": machine,
                    "label": str((member or {}).get("label")
                                 or (studio.get("title") or machine).split(" · ")[0]),
                    "role": str((member or {}).get("role") or "agent"),
                    "reachable": reachable,
                    "version": version,
                    "health": health,
                    "detail": detail,
                }
            stats = measured.get(machine) or {}
            rows.append({
                **row,
                "address": (member or {}).get("address"),
                "memory_gb": host.get("total_gb"),
                "available_gb": host.get("available_gb"),
                "disk_free_gb": host.get("disk_free_gb"),
                "disk_total_gb": host.get("disk_total_gb"),
                "chip": host.get("chip"),
                "jobs": stats.get("count", 0),
                "average_seconds": stats.get("avg_s"),
                "modalities": stats.get("modalities") or {},
            })

        # A member this Mac runs no studio for. A node with no worker started, or a controller in a
        # group whose roster this Mac read back, is still part of the pool and must be counted -
        # leaving it out is how a group of four Macs reports itself as one.
        for machine, member in members.items():
            if machine in seen or machine == "local":
                continue
            seen.add(machine)
            reachable = member.get("state") == "reachable"
            studios = member.get("studios") or []
            modalities: dict[str, int] = {}
            for entry in studios:
                modality = str(entry.get("modality") or "")
                if modality:
                    modalities[modality] = modalities.get(modality, 0) + 1
            # A member this Mac probed carries its own host facts here; one learned from a
            # controller's roster does not, and stays blank rather than borrowing this Mac's.
            host = member.get("host") or {}
            rows.append({
                "machine": machine,
                "label": str(member.get("label") or machine),
                "role": str(member.get("role") or "agent"),
                "address": member.get("address"),
                "reachable": reachable,
                "version": member.get("version"),
                "health": "up" if reachable else str(member.get("state") or "unknown"),
                "detail": member.get("detail"),
                "memory_gb": host.get("total_gb"), "available_gb": host.get("available_gb"),
                "disk_free_gb": host.get("disk_free_gb"),
                "disk_total_gb": host.get("disk_total_gb"), "chip": host.get("chip"),
                "jobs": (measured.get(machine) or {}).get("count", 0),
                "average_seconds": (measured.get(machine) or {}).get("avg_s"),
                "modalities": modalities,
            })

        return {
            "machines": rows,
            "role": pool["role"],
            "member": bool(pool.get("controller")),
            "controller": pool.get("controller"),
            "roster_readable": pool.get("readable", True),
            "summary": {
                "total": len(rows),
                "reachable": sum(row["reachable"] for row in rows),
                "unreachable": sum(not row["reachable"] for row in rows),
            },
        }

    @app.get("/api/hub/fleet/nodes")
    async def fleet_nodes() -> dict[str, Any]:
        from .backend import fleet_nodes as fleet
        return await fleet.inventory()

    @app.post("/api/hub/fleet/adopt")
    def fleet_adopt(body: dict[str, Any]) -> dict[str, Any]:
        """Take on a fleet's credential, which is what joining one means.

        A controller will not send work to a machine whose peer snapshot it cannot trust, and
        trust starts with the credential: every Hub mints its own, so a node that joined with a
        controller's token still answers 401 to that controller until it adopts the same one.

        Adopting also records *which* controller this Mac joined, because taking the credential is
        the moment the group becomes a fact about this Mac. Without it the Hub knew it was holding
        somebody else's token but not whose, so nothing on this Mac could name its own pool - the
        address lived only in the app's preferences, where a reinstall or a second app window
        loses it while the credential stays.

        This is deliberately **not** in the fleet credential's allowlist. Adopting is how a Mac
        joins a fleet, so it must be the Mac owner's decision - requiring the Hub token, which
        never leaves this machine, keeps a fleet-token holder from moving a node between fleets
        or locking it out of its own.
        """
        from .backend import fleet_nodes as fleet
        from .backend import peers
        value = str(body.get("token") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{24,256}", value):
            raise HTTPException(400, "a fleet token is 24 or more letters, numbers, dashes or underscores")
        values: dict[str, Any] = {}
        # Which controller was joined, recorded with the credential it came from. Always written,
        # never left at its previous value: an adoption that does not name a controller leaves this
        # Mac *not knowing* which one it answers to, and a stale address from a previous group would
        # show the wrong Mac as the head of this one. Not knowing is recoverable - re-join with an
        # address - and being confidently wrong is not.
        address = body.get("controller_url")
        if address is not None and str(address).strip():
            try:
                values["parent_controller_url"] = fleet.validate_address(address)
            except ValueError as exc:
                raise HTTPException(400, f"controller_url {exc}") from exc
        else:
            values["parent_controller_url"] = None
        peers.set_fleet_token(value)
        # Record that this credential is someone else's. Promotion to controller reads this and
        # mints a fresh one, so a machine that was a node never ends up running a group on the
        # credential of the group it used to belong to.
        control_plane.save_settings({"fleet_credential_origin": "adopted", **values})
        return {"ok": True, "adopted": True,
                "controller_url": control_plane.load_settings().get("parent_controller_url")}

    @app.post("/api/hub/fleet/leave")
    def fleet_leave() -> dict[str, Any]:
        """Stop being a node: drop the borrowed credential and run on this Mac's own again.

        The mirror of adopting, and the answer to "this Mac is being retired or sold". A Mac that
        leaves a group must not keep the group's token: it is the credential of a machine somebody
        else owns, and it is the one thing that must not travel with a Mac out of the door. Minting
        a fresh token here makes the departure real rather than cosmetic - the old controller's
        credential stops working on this Mac immediately, so it cannot dispatch to it even while
        its roster still lists it.

        Like adopting, this needs the Hub token and stays outside the fleet credential's allowlist:
        a controller must not be able to un-join a node, because leaving is the Mac owner's
        decision and the local Hub token never leaves this machine.
        """
        from .backend import peers
        peers.set_fleet_token("")
        control_plane.save_settings({
            "fleet_credential_origin": "minted",
            "role": "standalone",
            "parent_controller_url": None,
        })
        return {"ok": True, "left": True}

    @app.post("/api/hub/fleet/nodes", status_code=201)
    async def fleet_join(body: dict[str, Any]) -> dict[str, Any]:
        from .backend import fleet_nodes as fleet
        try:
            node = fleet.join(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        # A node that just joined has to enter this controller's dispatch registry now.
        # The registry is otherwise loaded once at startup, so without this the node was
        # listed as reachable but no work routed to it until the Hub was restarted.
        monitor.reload_registry()
        return {"node": node}

    @app.delete("/api/hub/fleet/nodes/{node_id}")
    async def fleet_leave(node_id: str) -> dict[str, Any]:
        from .backend import fleet_nodes as fleet
        if not fleet.leave(node_id):
            raise HTTPException(404, "unknown node")
        monitor.reload_registry()
        return {"ok": True}

    # ── remote app-update release (controller side) ────────────────────────
    # GatewayKH pushes a signed release; this Mac verifies and mirrors it, and
    # its own nodes pull it.  The signature, not the token, is what authorises a
    # release: POST /release is fleet-reachable (a gateway holds only the Fleet
    # token), while installing the trusted gateway key is an owner action and
    # stays Hub-token only.  See backend/app_release.py for the byte contract.

    @app.get("/api/hub/release")
    def get_release() -> dict[str, Any]:
        """What this controller holds, plus the gateway key the owner installed.

        The ``release`` key is unchanged.  ``trusted_publisher`` is additive: the
        gateway key the owner installed with ``POST /api/hub/release/trust``, or
        null when none has been installed - so a Mac reading this route can say
        plainly whether it can accept a pushed release at all.
        """
        trusted = app_release.trusted_publisher()
        return {
            "release": app_release.load_release(),
            "trusted_publisher": None if trusted is None else {
                "publisher_id": trusted.get("publisher_id"),
                "fingerprint": trusted.get("fingerprint"),
            },
        }

    @app.post("/api/hub/release")
    def push_release(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return app_release.accept_release(body)
        except app_release.ReleaseRefused as exc:
            raise HTTPException(exc.status_code, exc.message) from exc

    @app.get("/api/hub/release/artifact")
    def release_artifact() -> FileResponse:
        descriptor = app_release.load_release()
        if descriptor is None:
            raise HTTPException(404, "no release is held")
        return FileResponse(
            app_release.ARTIFACT_FILE,
            media_type="application/octet-stream",
            headers={"X-Content-SHA256": str(descriptor.get("sha256") or "")},
        )

    @app.get("/api/hub/release/identity")
    def release_identity() -> dict[str, Any]:
        """The controller's publisher public key + fingerprint, for a node to pin."""
        return app_release.controller_identity()

    @app.post("/api/hub/release/trust")
    def trust_release_publisher(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return app_release.set_trusted_publisher(body)
        except app_release.ReleaseRefused as exc:
            raise HTTPException(exc.status_code, exc.message) from exc

    # ── machine memory policy ──────────────────────────────────────────────
    # One route pair for every Studio's idle-release choice, so the app never has to know
    # which worker is which. FleetMemoryControl reads each Studio through the shared
    # `GET/PUT /api/memory-policy` + `POST /api/memory/release` contract and reports
    # per-Studio residency back, including how much each Studio is holding right now.

    @app.get("/api/hub/memory/policy")
    async def memory_policy() -> dict[str, Any]:
        from .backend.memory_control import FleetMemoryControl
        return await FleetMemoryControl(monitor).inventory()

    @app.put("/api/hub/memory/policy")
    async def set_memory_policy(body: dict[str, Any]) -> dict[str, Any]:
        from .backend.memory_control import FleetMemoryControl
        try:
            return await FleetMemoryControl(monitor).set_mode(
                str(body.get("mode") or ""), body.get("studio_ids"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/hub/memory/release")
    async def release_memory(body: dict[str, Any] | None = None) -> dict[str, Any]:
        from .backend.memory_control import FleetMemoryControl
        try:
            return await FleetMemoryControl(monitor).release((body or {}).get("studio_ids"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/hub/model-packages/{package_id}/import")
    def import_model_package(package_id: str, body: dict[str, Any]) -> dict[str, Any]:
        from components import model_packages
        try:
            catalog = _model_catalog()
            manifest = catalog[package_id]
            model_packages.import_folder(manifest, Path(body.get("source_folder", "")), _model_library())
            return _model_state(manifest, catalog, monitor)
        except KeyError as exc:
            raise HTTPException(404, "unknown model package") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/hub/model-packages/{package_id}/download")
    def download_model_package(package_id: str, body: dict[str, Any]) -> dict[str, Any]:
        from . import model_downloads
        try:
            catalog = _model_catalog()
            manifest = catalog[package_id]
            sources = body.get("sources")
            if sources is not None and not isinstance(sources, dict):
                raise ValueError("sources must be a map of model paths to HTTPS mirrors")
            return model_downloads.start(package_id=package_id, manifest=manifest,
                                         catalog=catalog, library=_model_library(), sources=sources)
        except KeyError as exc:
            raise HTTPException(404, "unknown model package") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/hub/model-downloads/{run_id}")
    def model_download_status(run_id: str) -> dict[str, Any]:
        from . import model_downloads
        run = model_downloads.status(run_id)
        if run is None:
            raise HTTPException(404, "unknown model download")
        return run

    @app.delete("/api/hub/model-downloads/{run_id}")
    def cancel_model_download(run_id: str) -> dict[str, Any]:
        from . import model_downloads
        run = model_downloads.cancel(run_id)
        if run is None:
            raise HTTPException(404, "unknown model download")
        return run

    @app.post("/api/hub/model-packages/{package_id}/verify")
    def verify_model_package(package_id: str) -> dict[str, Any]:
        from components import model_packages
        try:
            catalog = _model_catalog()
            manifest = catalog[package_id]
            model_packages.verify_installed(manifest, _model_library(), catalog=catalog)
            return _model_state(manifest, catalog, monitor)
        except KeyError as exc:
            raise HTTPException(404, "unknown model package") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/hub/machine-label")
    def set_machine_label(body: dict[str, Any]) -> dict[str, Any]:
        """Name this Mac the way the operator wants it shown.

        The name is a label, not an identity: the Hub's machine key stays "local", routing and fleet
        registration never depend on it, and renaming a Mac after nodes have joined does not orphan
        anything. `registry.set_label` has existed for this the whole time; no route exposed it, so
        the only name an operator could ever have was their hostname.
        """
        from .backend import registry
        name = str(body.get("name") or "").strip()
        if len(name) > 120:
            raise HTTPException(400, "the name must be at most 120 characters")
        registry.set_label("local", name)
        return {"settings": control_plane.public_settings()}

    @app.get("/api/hub/shared-voices")
    def shared_voice_list() -> dict[str, Any]:
        """What this controller holds and how far each voice has reached its nodes.

        The push is to six controllers, not a hundred Macs: the gateway never talks to a node. This
        is the read that lets it report what actually happened per location instead of assuming.
        """
        return {"voices": shared_voices.list_voices(monitor),
                "deletions": shared_voices.list_deletions(monitor),
                "targets": shared_voices.targets(monitor)}

    @app.post("/api/hub/shared-voices", status_code=201)
    async def shared_voice_push(
        request: Request,
        response: Response,
        audio: UploadFile = File(...),
        name: str = Form(...),
        language: str = Form(...),
        gender: str = Form(...),
        license: str = Form(...),
        notes: str = Form(""),
        source_url: str = Form(""),
        transcript: str | None = Form(None),
        transcript_segments: str | None = Form(None),
        voice_id: str = Form(""),
        permission_acknowledged: bool = Form(False),
    ) -> dict[str, Any]:
        """Create or refresh one fleet-wide voice, then fan it out to every joined node.

        The id may be supplied or left to the Hub: a caller that already names a clip by its own
        content hash can send that (12 lowercase hex) and the same voice then carries one id on
        every controller, which is easier to reconcile than six. A caller that does not gets one.
        Either way the bytes decide identity - identical audio refreshes the voice already holding
        it rather than adding a second copy, and answers 200 instead of 201.

        ``?wait=true`` runs one fan-out pass before answering, for a caller that wants to report per
        location from this response; without it the reconciler finishes the job and retries whatever
        a node was not ready for. It is read from the query string rather than the multipart body,
        so a caller does not have to mix a control flag into the voice's own fields.
        """
        try:
            audio_bytes = await audio.read()
        except Exception as exc:
            raise HTTPException(400, {"code": "SHARED_VOICE_AUDIO_UNREADABLE",
                                      "detail": f"The uploaded reference could not be read: {exc}"}) from exc
        finally:
            await audio.close()
        segments: object = []
        if (transcript_segments or "").strip():
            try:
                segments = json.loads(transcript_segments)
            except ValueError as exc:
                raise HTTPException(400, {
                    "code": "SHARED_VOICE_SEGMENTS_INVALID",
                    "detail": "transcript_segments must be a JSON array of ordered segments.",
                }) from exc
        try:
            voice, created = shared_voices.upsert(
                audio_bytes=audio_bytes, filename=audio.filename or "reference.wav",
                name=name, language=language, gender=gender, license=license,
                notes=notes, source_url=source_url or None,
                transcript=transcript, transcript_segments=segments or None,
                permission_acknowledged=permission_acknowledged,
                voice_id=voice_id.strip() or None,
            )
        except shared_voices.SharedVoiceConflict as exc:
            raise HTTPException(409, {"code": "SHARED_VOICE_CONFLICT", "detail": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(400, {"code": "SHARED_VOICE_REFUSED", "detail": str(exc)}) from exc
        wait = str(request.query_params.get("wait", "")).strip().lower() in {"1", "true", "yes"}
        if wait:
            await shared_voices.sync_voice(monitor, voice["id"])
        else:
            shared_voices.start_sync(monitor, voice["id"])
        if not created:
            # A refresh changed nothing about the bytes, so "already there" is a 200. A caller
            # reporting per location should not read that as a new voice.
            response.status_code = 200
        return {"voice": shared_voices.serialize(shared_voices.get(voice["id"]) or voice, monitor),
                "created": created,
                "status": "created" if created else "refreshed"}

    @app.get("/api/hub/shared-voices/{voice_id}")
    def shared_voice_read(voice_id: str) -> dict[str, Any]:
        voice = shared_voices.get(voice_id)
        if not voice:
            raise HTTPException(404, "unknown shared voice")
        return {"voice": shared_voices.serialize(voice, monitor)}

    @app.get("/api/hub/shared-voices/{voice_id}/audio")
    def shared_voice_audio(voice_id: str) -> FileResponse:
        path = shared_voices.audio_path(voice_id)
        if path is None:
            raise HTTPException(404, "unknown shared voice audio")
        return FileResponse(path, media_type="application/octet-stream", filename=path.name)

    @app.delete("/api/hub/shared-voices/{voice_id}")
    async def shared_voice_delete(voice_id: str) -> dict[str, Any]:
        """Tombstone one voice across the fleet: a removal on six controllers, not a hundred Macs.

        Awaited rather than queued: a caller removing a voice wants to know whether it came off the
        nodes, and the per-node work is already parallel with a short timeout.
        """
        try:
            shared_voices.prepare_delete(voice_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown shared voice") from exc
        except shared_voices.SharedVoiceConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"deletion": await shared_voices.delete_voice(monitor, voice_id)}

    @app.post("/api/hub/shared-voices/{voice_id}/sync")
    async def shared_voice_sync(voice_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Ask again now, for a node that was off during the push."""
        voice = shared_voices.get(voice_id)
        if not voice:
            raise HTTPException(404, "unknown shared voice")
        targets = (body or {}).get("studio_ids")
        target_ids = {str(value) for value in targets} if isinstance(targets, list) else None
        return {"voice": await shared_voices.sync_voice(monitor, voice_id, target_ids)}

    @app.post("/api/hub/jobs")
    def submit_jobs(envelope: dict[str, Any]) -> dict[str, Any]:
        if not control_plane.accepts_customer_jobs():
            raise HTTPException(409, "This Hub is in agent mode; submit customer jobs to a controller.")
        client_request_id = envelope.get("clientRequestId")
        replay = (next((batch for batch in broker.batches.values()
                       if batch.get("client_request_id") == client_request_id), None)
                  if isinstance(client_request_id, str) else None)
        replay = replay or (ledger.load_batch_by_client_request_id(client_request_id)
                            if isinstance(client_request_id, str) else None)
        if replay is None:
            try:
                refusal = _submission_readiness(envelope, monitor)
            except ValueError as exc:
                raise HTTPException(503, str(exc)) from exc
            if refusal:
                raise HTTPException(409, refusal)
        result = broker.submit_batch(envelope)
        if result.get("code") == cloud_guard.REFUSAL_CODE:
            raise HTTPException(403, {"code": result["code"], "detail": result["error"]})
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result

    @app.get("/api/hub/jobs")
    def list_jobs() -> dict[str, Any]:
        batches = {batch["id"]: batch for batch in ledger.load_finished_batches()}
        batches.update(broker.batches)
        return {"batches": [broker.batch_summary(batch) for batch in sorted(
            batches.values(), key=lambda value: -value["created_at"]
        )]}

    @app.get("/api/hub/jobs/{batch_id}")
    def get_job(batch_id: str) -> dict[str, Any]:
        batch = broker.batches.get(batch_id) or ledger.load_batch(batch_id)
        if batch is None:
            raise HTTPException(404, "unknown batch")
        return _summary(batch)

    @app.post("/api/hub/jobs/{batch_id}/items/{item_index}/reconcile")
    async def reconcile_item(batch_id: str, item_index: int) -> dict[str, Any]:
        try:
            batch = broker.batches.get(batch_id) or ledger.load_batch(batch_id)
            if batch is not None and batch.get("modality") == "voice":
                return await broker.reconcile_voice_item(
                    batch_id, item_index, client=monitor._client
                )
            if batch is not None and batch.get("modality") == "music":
                return await broker.reconcile_music_item(
                    batch_id, item_index, client=monitor._client
                )
            return await broker.reconcile_image_item(
                batch_id, item_index, client=monitor._client
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/hub/jobs/{batch_id}/items/{item_index}/discard")
    async def discard_item(batch_id: str, item_index: int) -> dict[str, Any]:
        """Abandon one uncertain item, releasing the machine it has been pinning."""
        try:
            return broker.discard_uncertain_item(batch_id, item_index)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/hub/jobs/{batch_id}/items/{item_index}/retry")
    async def retry_item(batch_id: str, item_index: int) -> dict[str, Any]:
        """Put one failed item back in the queue for the dispatcher to run again."""
        try:
            return broker.retry_failed_item(batch_id, item_index)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    async def open_worker_artifact(worker: dict[str, Any], artifact_path: str):
        from .backend.peers import studio_request
        url, headers = studio_request(worker, artifact_path)
        client = httpx.AsyncClient(follow_redirects=False)
        try:
            response = await client.send(client.build_request("GET", url, headers=headers), stream=True)
            response.raise_for_status()
        except Exception:
            await client.aclose()
            raise
        return client, response

    @app.get("/api/hub/jobs/{batch_id}/items/{item_index}/artifact")
    async def job_artifact(batch_id: str, item_index: int):
        """Authenticated Hub proxy for a completed worker artifact."""
        batch = broker.batches.get(batch_id) or ledger.load_batch(batch_id)
        if batch is None:
            raise HTTPException(404, "unknown batch")
        item = next((row for row in batch["items"] if row.get("index") == item_index), None)
        artifact_url = (item or {}).get("worker_artifact_url") or (item or {}).get("artifact_url")
        if not item or item.get("state") != "done" or not artifact_url:
            raise HTTPException(404, "artifact is not available")
        worker = next((row for row in monitor.registry if row["id"] == item.get("studio")), None)
        if worker is None:
            raise HTTPException(503, "artifact worker is unavailable")
        suffix = broker.MODALITY.get(batch.get("modality"), (None, None, None))[2]
        expected = f"/api/generate/jobs/{item.get('studio_job_id')}/{suffix}"
        parsed = urlsplit(str(artifact_url))
        worker_origin = urlsplit(worker["base_url"])
        # A path recorded by this Hub is checked against the worker's origin *now*, not the one it
        # had when the job ran - which is why the stored value is a path and not a URL. An absolute
        # URL from a before-this-change batch is accepted only when it happens to name the worker
        # the item is still assigned to and the path this Hub would have written itself; anything
        # else is refused, as before, so a worker-supplied URL is never followed on trust.
        if not parsed.scheme and not parsed.netloc:
            same_origin = True
        else:
            same_origin = (parsed.scheme == worker_origin.scheme
                           and parsed.netloc == worker_origin.netloc
                           and parsed.path == expected)
        if (not suffix or not same_origin or parsed.query or parsed.fragment or parsed.path != expected):
            # Never follow a worker-supplied URL outside its private origin or
            # attach the worker credential to an arbitrary location.
            raise HTTPException(502, "worker artifact reference is invalid")
        release_reader = output_storage.acquire_reader(batch_id=batch_id, item_index=item_index)
        try:
            client, response = await open_worker_artifact(worker, expected)
        except httpx.HTTPError as exc:
            release_reader()
            raise HTTPException(502, "worker artifact could not be read") from exc

        async def close_stream() -> None:
            await response.aclose()
            await client.aclose()
            release_reader()

        media_type = item.get("media_type") or response.headers.get("content-type") or "application/octet-stream"
        headers = {}
        if item.get("bytes"):
            headers["Content-Length"] = str(item["bytes"])
        if item.get("sha256"):
            headers["X-Content-SHA256"] = str(item["sha256"])
        return StreamingResponse(response.aiter_bytes(1024 * 1024), media_type=media_type,
                                 headers=headers, background=BackgroundTask(close_stream))

    @app.delete("/api/hub/jobs/{batch_id}")
    async def cancel_job(batch_id: str) -> dict[str, Any]:
        result = await broker.cancel_batch(batch_id)
        if result is None:
            raise HTTPException(404, "unknown batch")
        return {"ok": True, **{key: value for key, value in result.items() if key != "batch"}}

    @app.post("/api/hub/transcription/jobs")
    async def create_transcription_job(
        files: list[UploadFile] = File(...), item_ids: list[str] = Form(...),
        model: str = Form(...), language: str | None = Form(None),
        word_timestamps: bool = Form(False), label: str | None = Form(None),
        project: str | None = Form(None), episode: str | None = Form(None),
        genstudio_execution: str | None = Form(None),
        routing: str | None = Form(None),
    ) -> dict[str, Any]:
        if not control_plane.accepts_customer_jobs():
            raise HTTPException(409, "This Hub is in agent mode; submit transcription to a controller.")
        parsed, routing_error = routing_rule.parse(routing)
        if routing_error:
            raise HTTPException(400, routing_error)
        if parsed.startswith(routing_rule.MACHINE_PREFIX):
            # Pinning a machine that is not in the fleet is refused at submission, like the
            # other modalities, rather than left to sit in the queue with nothing to say.
            known = {s.get("machine", "local") for s in monitor.registry}
            target = parsed.split(":", 1)[1]
            if target not in known:
                raise HTTPException(400, f"no machine named {target!r} is in this fleet; known: "
                                         + ", ".join(sorted(known)))
        try:
            batch, duplicate = await transcription_jobs.create_batch(
                files, item_ids, model, language, word_timestamps, label, project,
                episode, genstudio_execution,
                routing=parsed,
                admission_guard=lambda envelope: _submission_readiness(envelope, monitor),
            )
        finally:
            for upload in files:
                await upload.close()
        if start_background:
            transcription_jobs.start_dispatcher(monitor)
        return {"batch_id": batch["id"], "items": len(batch["items"]),
                "queued": sum(item["state"] == "queued" for item in batch["items"]),
                "duplicate": duplicate}

    @app.post("/api/hub/voice-references", status_code=201)
    async def stage_voice_reference(
        audio: UploadFile = File(...),
        transcript: str = Form(""),
        transcript_segments: str = Form(""),
        language: str = Form(""),
        ttl_seconds: float | None = Form(None),
        source_asset_id: str = Form(""),
        sha256: str = Form(""),
    ) -> dict[str, Any]:
        """Stage one clone reference for later Voice jobs — one contract, two origins.

        Local origin: the app uploads a chosen clip and omits ``source_asset_id``.
        GenStudio origin: the controller pushes the clip with its durable
        ``source_asset_id``, which keeps the existing required-source semantics.

        Either way the returned ``asset_id`` is passed back verbatim as
        ``voice_reference_asset_id`` on ``/api/hub/jobs``; the Hub then streams
        the staged bytes to the Voice worker's clone route.  Nothing about the
        local origin relaxes GenStudio's type/size/checksum/transcript/expiry
        and privacy rules — only the upstream identity is optional.
        """
        from .backend import execution_assets
        try:
            audio_bytes = await audio.read()
        except Exception as exc:
            raise HTTPException(400, {"code": "REFERENCE_AUDIO_UNREADABLE",
                                      "detail": f"The uploaded reference could not be read: {exc}"}) from exc
        finally:
            await audio.close()
        segments: object = []
        if transcript_segments.strip():
            try:
                segments = json.loads(transcript_segments)
            except ValueError as exc:
                raise HTTPException(400, {
                    "code": "REFERENCE_TRANSCRIPT_SEGMENTS_INVALID",
                    "detail": "transcript_segments must be a JSON array of ordered segments.",
                }) from exc
        stage = (execution_assets.stage_voice_reference if source_asset_id.strip()
                 else execution_assets.stage_local_voice_reference)
        try:
            metadata = stage(
                audio_bytes=audio_bytes,
                filename=audio.filename or "reference.wav",
                declared_sha256=sha256,
                transcript=transcript,
                transcript_segments=segments,
                language=language,
                ttl_seconds=(execution_assets.DEFAULT_TTL_SECONDS if ttl_seconds is None
                             else int(ttl_seconds)),
                **({"source_asset_id": source_asset_id} if source_asset_id.strip() else {}),
            )
        except execution_assets.ExecutionAssetError as exc:
            raise HTTPException(400, {"code": exc.code, "detail": exc.detail}) from exc
        public = execution_assets.public(metadata)
        return {
            "asset_id": public["id"],
            "sha256": public["sha256"],
            "bytes": public["bytes"],
            "media_type": public["media_type"],
            "created_at": public["created_at"],
            "expires_at": public["expires_at"],
            "transcript_supplied": bool(transcript.strip() or segments),
            "origin": "genstudio" if source_asset_id.strip() else "local",
        }

    @app.get("/api/hub/voice-references/{asset_id}")
    def voice_reference_status(asset_id: str) -> dict[str, Any]:
        """Report whether a staged clone reference is still usable."""
        from .backend import execution_assets
        try:
            metadata, audio_bytes = execution_assets.read_voice_reference(asset_id)
        except execution_assets.ExecutionAssetError as exc:
            raise HTTPException(409, {"code": exc.code, "detail": exc.detail}) from exc
        return {
            "asset_id": asset_id,
            "sha256": metadata.get("sha256"),
            "bytes": len(audio_bytes),
            "media_type": execution_assets.MIME_BY_EXTENSION.get(
                str(metadata.get("audio_extension") or ""), "application/octet-stream"
            ),
            "created_at": metadata.get("created_at"),
            "expires_at": metadata.get("expires_at"),
            "transcript_supplied": bool(
                str(metadata.get("transcript") or "").strip()
                or metadata.get("transcript_segments")
            ),
        }

    @app.get("/api/hub/transcription/jobs")
    def list_transcription_jobs() -> dict[str, Any]:
        return {"batches": transcription_jobs.list_batches(), "stats": transcription_jobs.statistics()}

    @app.get("/api/hub/transcription/jobs/{batch_id}")
    def get_transcription_job(batch_id: str) -> dict[str, Any]:
        batch = transcription_jobs.get_batch(batch_id)
        if batch is None:
            raise HTTPException(404, "unknown transcription batch")
        return transcription_jobs.summary(batch, include_metadata=True)

    @app.get("/api/hub/transcription/jobs/{batch_id}/items/{item_index}/artifact")
    def transcription_artifact(batch_id: str, item_index: int):
        batch = transcription_jobs.get_batch(batch_id)
        if batch is None:
            raise HTTPException(404, "unknown transcription batch")
        item = next((row for row in batch["items"] if row.get("index") == item_index), None)
        path = Path((item or {}).get("artifact_path") or "")
        try:
            safe = path.resolve().is_relative_to(transcription_jobs.ROOT.resolve())
        except OSError:
            safe = False
        if (not item or item.get("state") != "done" or not safe or not path.is_file()
                or path.stat().st_size == 0):
            raise HTTPException(404, "SRT artifact is not available")
        return FileResponse(path, media_type="application/x-subrip",
                            filename=f"{item['item_id']}.srt")

    @app.post("/api/hub/transcription/jobs/{batch_id}/items/{item_index}/ack")
    async def acknowledge_transcription(batch_id: str, item_index: int) -> dict[str, Any]:
        """Verified-delivery receipt for one Subtitle artifact, mirroring the Image ack."""
        return await transcription_jobs.acknowledge_item(monitor, batch_id, item_index)

    @app.post("/api/hub/transcription/jobs/{batch_id}/items/{item_index}/retry")
    async def retry_transcription_item(batch_id: str, item_index: int) -> dict[str, Any]:
        """Put one failed transcription back in the queue for the dispatcher."""
        try:
            return transcription_jobs.retry_failed_item(batch_id, item_index)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/hub/transcription/jobs/{batch_id}")
    async def cancel_transcription_job(batch_id: str) -> dict[str, Any]:
        batch = await transcription_jobs.cancel_batch(batch_id)
        if batch is None:
            raise HTTPException(404, "unknown transcription batch")
        return transcription_jobs.summary(batch)

    # ── the fleet peer surface ─────────────────────────────────────────────
    # A controller dispatches by talking to a peer Hub at /studio/<modality>/<path>, exactly
    # as this Hub talks to its own workers. Serving that surface is what makes a packaged app a
    # valid fleet member: the rest of the fleet logic already exists on the controller side and
    # only ever needed somewhere to point.
    WORKER_FOR_MODALITY = {
        "image": "image", "voice": "voice", "transcription": "subtitle", "music": "music",
        "chat": "chat",
    }

    def _proxy_target(path: str) -> tuple[str, str]:
        """Resolve a peer path to a local worker, or refuse it.

        The path is appended to a worker address this Hub owns, so it can only ever reach a
        local studio - but a traversal or an absolute URL would still let a caller reach worker
        routes it was never meant to, so both are refused outright rather than normalised.
        """
        cleaned = path.strip("/")
        if (not cleaned or ".." in cleaned.split("/")
                or "//" in cleaned or ":" in cleaned.split("/")[0]
                or "?" in cleaned or "#" in cleaned):
            raise HTTPException(400, "invalid studio path")
        segments = cleaned.split("/")
        modality = segments[0]
        worker_id = WORKER_FOR_MODALITY.get(modality)
        if worker_id is None:
            raise HTTPException(404, "unknown studio")
        return worker_id, "/" + "/".join(segments[1:])

    @app.api_route("/studio/{modality}/{path:path}",
                   methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def studio_proxy(modality: str, path: str, request: Request) -> Response:
        """Forward one fleet request to this Mac's own worker for that studio.

        The caller's credential is not passed on. The worker trusts only this Hub, so the
        request is re-signed with the worker's own token: a fleet credential reaching this Mac
        must never become a fleet credential reaching its workers.
        """
        worker_id, tail = _proxy_target(f"{modality}/{path}")
        worker = next((row for row in monitor.registry if row["id"] == worker_id), None)
        if worker is None:
            raise HTTPException(503, f"{worker_id.title()} worker is unavailable")
        body = await request.body()
        content_type = request.headers.get("content-type") or ""
        owner = None
        _prune_peer_leases()
        if _peer_work_request(request.method, path):
            model = await _request_model(request, body, content_type)
            refusal = _peer_admission_refusal(worker, monitor, model)
            if refusal is not None:
                return _peer_refusal_response(refusal)
            machine = str(worker.get("machine") or "local")
            owner = _reserve_peer_machine(worker_id, machine)
            if owner is None:
                return _peer_refusal_response(
                    {"code": "MACHINE_BUSY",
                     "detail": f"{machine} is reserved for other work on this Mac"})
        url = worker["base_url"] + tail
        if request.url.query:
            url += "?" + request.url.query
        headers = {"X-Hub-Token": worker["hub_token"]}
        if content_type:
            headers["Content-Type"] = content_type
        try:
            upstream = await monitor._client.request(
                request.method, url, headers=headers, content=body or None,
                timeout=httpx.Timeout(900.0, connect=5.0),
            )
        except (httpx.HTTPError, OSError) as exc:
            if owner is not None:
                _release_peer_lease(owner)
            raise HTTPException(502, f"{worker_id.title()} worker request failed") from exc
        if owner is not None:
            _settle_peer_lease(owner, worker_id, upstream)
        _observe_peer_response(worker_id, tail, upstream)
        passthrough = {}
        for name in ("content-type", "content-disposition", "x-content-sha256"):
            if name in upstream.headers:
                passthrough[name] = upstream.headers[name]
        return Response(content=upstream.content, status_code=upstream.status_code,
                        headers=passthrough)

    @app.post("/api/hub/chat/completions")
    async def hub_chat_completions(request: Request) -> Response:
        """Local Chat completion for this app's own Chat page.

        Reuses the peer surface's worker lookup, admission reservation and lease settlement:
        the app talks to its own Hub, and the Hub re-signs the request with the worker token.
        """
        return await studio_proxy("chat", "v1/chat/completions", request)

    from . import delivery, operator_controls, statistics
    delivery.register_routes(app, monitor)
    operator_controls.register_routes(app, monitor)
    statistics.register_routes(app, monitor)
    return app


app = create_app()
