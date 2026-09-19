"""Private standalone Subtitle API.

One owning Hub credential, one loopback port, its own job ledger and its own
artifact tree. The Voice-compatible ``/api/transcribe`` and
``/api/transcribe/availability`` shapes let the existing Hub transcription
lifecycle drive this worker the same way it drives Voice's STT subsystem;
everything under ``/api/generate/transcribe`` is the Subtitle Studio's own
public lifecycle.
"""
from __future__ import annotations

import hmac
import os
from pathlib import Path
import tempfile

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from . import memory_policy
from .engine import ModelPackageError, host_eligibility
from .manager import SubtitleManager, safe_input_name, validate_request


UPLOAD_CHUNK_BYTES = 1024 * 1024


async def read_upload_limited(file, limit: int) -> bytes:
    """Read an upload, refusing the moment it passes the limit.

    `await file.read()` returned the entire body first and the size was checked on the result, so an
    oversized upload was buffered in this process before being refused - and this worker's limit is
    2 GiB, on a Mac that is also holding a model in unified memory. Reading in chunks stops at the
    first byte past the limit, so the refusal costs one chunk instead of the whole file.
    """
    from fastapi import HTTPException as _HTTPException
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise _HTTPException(
                413, f'upload exceeds the {limit // (1024 * 1024)} MB per-file limit')
        chunks.append(chunk)
    return b"".join(chunks)


def create_app(manager: SubtitleManager, token: str) -> FastAPI:
    if not token:
        raise ValueError('Private Subtitle worker requires its owning Hub credential')
    app = FastAPI(title='Studio Hub Subtitle', version='1')
    # One resident Whisper model, released when idle. Without this a Subtitle model stayed
    # loaded for the life of the process, which is the memory that matters on an 8 GB Mac.
    memory_policy.start_background(manager)

    @app.middleware('http')
    async def require_owner(request: Request, call_next):
        offered = (request.headers.get('x-studio-token') or request.headers.get('x-hub-token')
                   or request.headers.get('authorization', '').removeprefix('Bearer '))
        if not hmac.compare_digest(offered, token):
            return JSONResponse({'detail': 'Owner credential required'}, status_code=401)
        return await call_next(request)

    def public_job(job: dict | None) -> dict | None:
        if job is None:
            return None
        value = dict(job)
        value.pop('input', None)
        return value

    # ── health / catalog ───────────────────────────────────────────────────

    @app.get('/api/health')
    def health() -> dict:
        active = [job for job in manager.list() if job['state'] in ('queued', 'running')]
        return {'ok': True, 'app_version': 'standalone', 'transcription': {
            'available': True,
            'busy': bool(active),
            'queued': sum(job['state'] == 'queued' for job in active),
            'running': sum(job['state'] == 'running' for job in active),
            'runtime_revision': manager.engine.runtime_sha256,
            'loaded_package': manager.engine.loaded_package}}

    @app.get('/api/catalog')
    def catalog() -> dict:
        return {'models': manager.catalog(),
                'default_model': manager.default_package(),
                'export_source': manager.engine.export_source(),
                'host': host_eligibility()}

    # ── model memory policy ────────────────────────────────────────────────
    # Path names are the fleet contract every Studio publishes, matching the Voice worker's
    # `GET/PUT /api/memory-policy` and `POST /api/memory/release`. The Hub's
    # FleetMemoryControl reads all of them through this one shape.

    @app.get('/api/memory-policy')
    def memory_policy_status() -> dict:
        return memory_policy.status()

    @app.put('/api/memory-policy')
    def memory_policy_save(body: dict | None = None) -> dict:
        try:
            return memory_policy.save(str((body or {}).get('mode') or ''))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post('/api/memory/release')
    def memory_release() -> dict:
        try:
            return memory_policy.release_now('manual')
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get('/api/transcribe/availability')
    def transcribe_availability() -> dict:
        return manager.availability()

    # ── synchronous Hub transcription contract ─────────────────────────────

    @app.post('/api/transcribe')
    async def transcribe(
        file: UploadFile = File(...),
        model: str = Form(''),
        language: str = Form(''),
        word_timestamps: bool = Form(False),
        activity_id: str = Form(''),
    ) -> dict:
        """Transcribe one upload synchronously: text + segments + SRT + VTT.

        Shape and error mapping match the retained Voice endpoint, so the Hub's
        transcription lifecycle can target either worker without a new job API.
        """
        try:
            name = safe_input_name(file.filename or '')
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        data = await read_upload_limited(file, manager.MAX_INPUT_BYTES)
        try:
            request = validate_request(
                {'model': model or manager.default_package(),
                 'language': language or None,
                 'word_timestamps': bool(word_timestamps),
                 'formats': ['srt', 'vtt', 'text']},
                packages=sorted(manager.engine.manifests),
                default_package=manager.default_package(),
                aliases=manager.model_aliases())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        readiness = manager.readiness(request['model'])
        if not readiness['ready']:
            raise HTTPException(409, readiness['reason']
                                or f'{request["model"]} is not ready')
        source = manager.store_input(
            (activity_id.strip() or 'sync')[:32], name, data)
        job = manager.submit(request, source, activity_id=activity_id.strip() or None)
        result = await _await_job(manager, job['id'])
        if result['state'] != 'done':
            raise HTTPException(409 if result.get('error_code') in
                                ('MODEL_FILES_UNAVAILABLE', 'TRANSCRIPTION_UNAVAILABLE')
                                else 422,
                                result.get('error') or 'transcription failed')
        artifacts = result.get('artifacts') or {}
        payload = {
            'text': result.get('text'), 'language': result.get('language'),
            'duration': result.get('media_duration_seconds'),
            'model': result.get('model'),
            # The segments the engine already produced, not `None`. A Qwen3-TTS Base clone needs a
            # timestamped transcript to pick a bounded window out of a reference longer than its
            # 15-second maximum, and the app's Transcribe button is how that transcript is
            # obtained. Dropping the timestamps here is what forced the operator to paste words
            # that the engine had just measured.
            'segments': [{'start': s.get('start'), 'end': s.get('end'), 'text': s.get('text')}
                         for s in (result.get('segments') or [])],
            'srt': _read(artifacts, 'srt'), 'vtt': _read(artifacts, 'vtt'),
            'elapsed_seconds': result.get('duration_seconds'),
            'studio_task_id': result['id'], 'task_id': result['id'],
            'job_id': result['id'],
            'model_revision': result.get('model_revision'),
            'package_id': result.get('package_id'),
        }
        return payload

    # ── Subtitle Studio public lifecycle ───────────────────────────────────

    @app.post('/api/generate/transcribe')
    async def submit(
        file: UploadFile = File(...),
        model: str = Form(''),
        language: str = Form(''),
        word_timestamps: bool = Form(False),
        formats: str = Form('srt,vtt,text'),
        activity_id: str = Form(''),
    ) -> dict:
        try:
            name = safe_input_name(file.filename or '')
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        data = await read_upload_limited(file, manager.MAX_INPUT_BYTES)
        try:
            request = validate_request(
                {'model': model or manager.default_package(),
                 'language': language or None,
                 'word_timestamps': bool(word_timestamps),
                 'formats': [item.strip() for item in formats.split(',') if item.strip()]},
                packages=sorted(manager.engine.manifests),
                default_package=manager.default_package(),
                aliases=manager.model_aliases())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        source = manager.store_input('studio', name, data)
        try:
            job = manager.submit(request, source,
                                 activity_id=activity_id.strip() or None)
        except ModelPackageError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {'job': public_job(job)}

    @app.get('/api/generate/transcribe/jobs')
    def jobs() -> dict:
        return {'jobs': [public_job(job) for job in manager.list()]}

    @app.get('/api/generate/transcribe/jobs/{job_id}')
    def job(job_id: str) -> dict:
        value = manager.get(job_id)
        if value is None:
            raise HTTPException(404, 'job not found')
        return {'job': public_job(value)}

    @app.delete('/api/generate/transcribe/jobs/{job_id}')
    def cancel(job_id: str) -> dict:
        value = manager.cancel(job_id)
        if value is None:
            raise HTTPException(404, 'job not found')
        return {'job': public_job(value)}

    @app.get('/api/generate/transcribe/jobs/{job_id}/artifact')
    def artifact(job_id: str, format: str = 'srt'):
        found = manager.artifact_path(job_id, format)
        if found is None:
            raise HTTPException(404, 'subtitle artifact is unavailable')
        path, media_type, suffix = found
        value = manager.get(job_id) or {}
        receipt = (value.get('artifacts') or {}).get(format) or {}
        return FileResponse(path, media_type=media_type,
                            filename=f'{job_id}.{suffix}',
                            headers={'X-Content-SHA256': str(receipt.get('sha256') or '')})

    @app.post('/api/generate/transcribe/jobs/{job_id}/ack')
    def ack(job_id: str) -> dict:
        receipt = manager.ack(job_id)
        if receipt is None:
            raise HTTPException(404, 'completed Subtitle job not found')
        return receipt

    return app


def _read(artifacts: dict, fmt: str) -> str:
    receipt = artifacts.get(fmt)
    if not receipt:
        return ''
    try:
        return Path(receipt['path']).read_text(encoding='utf-8')
    except OSError:
        return ''


async def _await_job(manager: SubtitleManager, job_id: str) -> dict:
    """Wait for one synchronous request's job to reach a terminal state."""
    import asyncio
    while True:
        value = manager.get(job_id)
        if value is None:
            raise HTTPException(404, 'job not found')
        if value['state'] in ('done', 'error', 'cancelled'):
            return value
        await asyncio.sleep(0.05)


def from_environment() -> FastAPI:
    import json
    data = Path(os.environ['STUDIOHUB_SUBTITLE_DATA_DIR'])
    library = Path(os.environ['STUDIOHUB_MODELS_DIR'])
    voice_app = Path(os.environ['STUDIOHUB_VOICE_APP_DIR'])
    manifest_dir = Path(os.environ['STUDIOHUB_SUBTITLE_MANIFESTS'])
    manifest_paths = sorted(manifest_dir.glob('whisper-*.json'))
    if not manifest_paths:
        raise ValueError('Subtitle model catalog is missing from the bundle')
    manifests = [json.loads(path.read_text(encoding='utf-8')) for path in manifest_paths]
    from components import model_packages
    for manifest in manifests:
        model_packages.validate_manifest(manifest)
        if manifest['engine'] != 'subtitle-mlx':
            raise ValueError(f'{manifest["package_id"]} is not a subtitle package')
    release = json.loads(Path(os.environ['STUDIOHUB_RELEASE_MANIFEST']).read_text())
    identity = (release.get('components', {}).get('subtitle') or {}).get('runtime_sha256')
    if not isinstance(identity, str) or len(identity) != 64:
        raise ValueError('Release manifest lacks exact Subtitle runtime identity')
    return create_app(
        SubtitleManager(data, library, manifests, identity, voice_app_dir=voice_app,
                        offline=os.environ.get('HF_HUB_OFFLINE') == '1'),
        os.environ['STUDIOHUB_FLEET_TOKEN'])
