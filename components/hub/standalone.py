"""The packaged, private Studio Hub API surface.

This module intentionally exposes the durable job, transcription and settings
contracts. Legacy Pinokio control, updater and fleet-repair routes are not
registered by the packaged application.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from . import output_storage, standalone_config
from .backend import auth, broker, cloud_guard, control_plane, ledger, transcription_jobs
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


def _model_state(manifest: dict[str, Any], catalog: dict[str, dict[str, Any]], monitor: StudioMonitor) -> dict[str, Any]:
    from components import model_packages
    receipt = model_packages.preflight(manifest, _model_library(), catalog=catalog)
    worker_id = {"image-mflux": "image", "voice-mlx": "voice", "subtitle-mlx": "subtitle"}[manifest["engine"]]
    worker = next((item for item in monitor.registry if item["id"] == worker_id), None)
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
        "worker_id": worker_id,
        "worker_state": "running" if worker else "unavailable",
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
        "image-mflux": "image", "voice-mlx": "voice", "subtitle-mlx": "transcription",
    }.get(manifest["engine"])
    if expected_modality != envelope.get("modality"):
        return {"code": "MODEL_MODALITY_MISMATCH", "detail": "model is not available for this job modality"}
    state = _model_state(manifest, catalog, monitor)
    if state["dispatch_state"] != "eligible":
        return {"code": "MODEL_NOT_READY", "detail": "model files, runtime, OS, or chip are not ready",
                "readiness": state}
    settings = standalone_config.load_device_settings()["effective"]
    workers = [worker for worker in monitor.registry
               if worker.get("modality") == expected_modality and worker.get("local") is True]
    blocked = [output_storage.admission(machine=worker.get("machine", "local"), max_bytes=settings["max_bytes"],
               retention_days=settings["retention_days"], enabled=settings["cleanup_enabled"],
               minimum_free_bytes=settings["minimum_free_bytes"],
               managed_output_roots=worker.get("managed_output_roots"))
               for worker in workers]
    blocked = [refusal for refusal in blocked if refusal]
    if workers and len(blocked) == len(workers):
        return blocked[0]
    return None


def create_app(*, start_background: bool = True) -> FastAPI:
    """Create one owner-authenticated Hub bound to packaged worker descriptors."""
    monitor = StudioMonitor()
    broker.configure_monitor(monitor)
    broker.configure_admission_guard(lambda batch: _submission_readiness(batch, monitor))
    cloud_guard.configure_monitor(monitor)
    token = auth.load_token()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ledger.prepare_database()
        output_storage.prepare()
        output_storage.recover_process_leases()
        broker.restore_batches()
        transcription_jobs.restore_batches()
        if start_background:
            output_storage.start_background(
                lambda: {worker.get("machine", "local") for worker in monitor.registry},
                standalone_config.load_device_settings,
            )
            monitor.start()
            broker.start_dispatcher()
            transcription_jobs.start_dispatcher(monitor)
            await control_plane.runtime.start(monitor, _app_version())
        try:
            yield
        finally:
            if start_background:
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
        return {"version": _app_version(), "source": "studiohub-mac@06e893e1643bffbcbd162fae122e5a4d0f29e283"}

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
        return await monitor.aggregate_catalog()

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

    @app.get("/api/hub/fleet/nodes")
    async def fleet_nodes() -> dict[str, Any]:
        from .backend import fleet_nodes as fleet
        return await fleet.inventory()

    @app.post("/api/hub/fleet/nodes", status_code=201)
    async def fleet_join(body: dict[str, Any]) -> dict[str, Any]:
        from .backend import fleet_nodes as fleet
        try:
            node = fleet.join(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"node": node}

    @app.delete("/api/hub/fleet/nodes/{node_id}")
    async def fleet_leave(node_id: str) -> dict[str, Any]:
        from .backend import fleet_nodes as fleet
        if not fleet.leave(node_id):
            raise HTTPException(404, "unknown node")
        return {"ok": True}

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
    async def reconcile_image(batch_id: str, item_index: int) -> dict[str, Any]:
        try:
            return await broker.reconcile_image_item(batch_id, item_index, client=monitor._client)
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
        parsed = urlsplit(str(artifact_url))
        suffix = broker.MODALITY.get(batch.get("modality"), (None, None, None))[2]
        expected = f"/api/generate/jobs/{item.get('studio_job_id')}/{suffix}"
        worker_origin = urlsplit(worker["base_url"])
        same_origin = ((not parsed.scheme and not parsed.netloc)
                       or (parsed.scheme == worker_origin.scheme and parsed.netloc == worker_origin.netloc))
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
    ) -> dict[str, Any]:
        if not control_plane.accepts_customer_jobs():
            raise HTTPException(409, "This Hub is in agent mode; submit transcription to a controller.")
        try:
            batch, duplicate = await transcription_jobs.create_batch(
                files, item_ids, model, language, word_timestamps, label, project,
                episode, genstudio_execution,
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
            metadata, path = execution_assets.resolve_voice_reference(asset_id)
        except execution_assets.ExecutionAssetError as exc:
            raise HTTPException(409, {"code": exc.code, "detail": exc.detail}) from exc
        return {
            "asset_id": asset_id,
            "sha256": metadata.get("sha256"),
            "bytes": path.stat().st_size,
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

    @app.delete("/api/hub/transcription/jobs/{batch_id}")
    async def cancel_transcription_job(batch_id: str) -> dict[str, Any]:
        batch = await transcription_jobs.cancel_batch(batch_id)
        if batch is None:
            raise HTTPException(404, "unknown transcription batch")
        return transcription_jobs.summary(batch)

    from . import delivery, operator_controls, statistics
    delivery.register_routes(app, monitor)
    operator_controls.register_routes(app, monitor)
    statistics.register_routes(app, monitor)
    return app


app = create_app()
