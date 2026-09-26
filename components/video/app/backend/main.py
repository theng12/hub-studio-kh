"""Private standalone Video API for the registered video models.

The lane speaks two doors, and both are the Hub's own contract rather than an invention here:

* ``POST /api/generate/txt2video`` — JSON, the broker's ``MODALITY`` table names this exact
  endpoint for a text-to-video item.
* ``POST /api/generate/video2video`` — multipart ``file`` plus the flattened form fields the
  broker's image-to-video path sends (``mode=img2video``), gated on this model publishing the
  ``img2video`` capability.

A job is accepted before the result exists and returns its receipt immediately: video is
``RETRY_CLASS_DURABLE_ASYNC`` in the Hub, which requires exactly that, because a five-minute
job must survive a Hub restart without being blindly replayed.
"""
from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import platform
import subprocess
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from . import memory_policy
from .generation import (CANVAS_CLASSES, MODELS, MODEL_ID, GenerationManager,
                         lane_contract, lane_parameters, final_matches, host_memory_bytes,
                         memory_eligible, missing_runtime_model_reason, muxer_problem)

from components import app_version, model_defaults


DEFAULT_MODEL = MODELS[MODEL_ID]

# One upload ceiling for a source image. It is the same 25 MB the gateway's transcription
# door uses, and it is deliberately a refusal rather than a silent truncation: a scene PNG is
# a few megabytes, and anything past this is not a frame of an episode.
MAX_SOURCE_IMAGE_BYTES = 25 * 1024 * 1024


def _host_facts() -> dict:
    """What this Mac is, before any model is considered."""
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        return {'eligible': False, 'reason': 'Apple Silicon macOS is required'}
    try:
        memory = int(subprocess.check_output(['/usr/sbin/sysctl', '-n', 'hw.memsize'],
                                             text=True, timeout=3).strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'eligible': False, 'reason': 'Unified memory could not be measured'}
    version = platform.mac_ver()[0]
    if not version:
        return {'eligible': False, 'reason': 'macOS version could not be measured'}
    if int(version.split('.')[0]) < 14:
        return {'eligible': False, 'reason': 'macOS 14 or later is required'}
    return {'eligible': True, 'macos': version, 'unified_memory_bytes': memory}


def memory_warning(model, host: dict) -> str | None:
    """The tier warning for one model on one host, or None when the host meets the tier."""
    memory = host.get('unified_memory_bytes') or 0
    if not host.get('eligible') or not memory or memory_eligible(model, memory):
        return None
    return (f'This Mac has {memory / 1024**3:.0f} GB of unified memory. {model.label} is '
            f'qualified at {model.qualified_minimum_memory_gb:g} GB and above, so it is not '
            f'offered here: the measured process footprint is the reason for the tier, and the '
            f'load would swap rather than fit.')


def create_app(manager: GenerationManager, token: str) -> FastAPI:
    if not token:
        raise ValueError('Private Video worker requires its owning Hub credential')
    app = FastAPI(title='Studio Hub Video', version='1')

    @app.middleware('http')
    async def require_owner(request: Request, call_next):
        offered = (request.headers.get('x-studio-token') or request.headers.get('x-hub-token')
                   or request.headers.get('authorization', '').removeprefix('Bearer '))
        if not hmac.compare_digest(offered, token):
            return JSONResponse({'detail': 'Owner credential required'}, status_code=401)
        return await call_next(request)

    def model_state(model) -> dict:
        """One model's readiness: its own installed files and this host's tier."""
        files = manager.preflight(model.repo)
        host = _host_facts()
        tier_ok = memory_eligible(model, host.get('unified_memory_bytes') or 0)
        muxer = muxer_problem() if model.runnable else None
        ready = (files['state'] == 'files_verified' and host['eligible'] and tier_ok
                 and model.runnable and muxer is None)
        return {'ready': ready, 'files': files, 'host': host, 'tier_eligible': tier_ok,
                'muxer_problem': muxer,
                'runtime_identity': {'id': manager.runtime_sha256, 'compatible': True}}

    def readiness() -> dict:
        return model_state(DEFAULT_MODEL)

    @app.get('/api/health')
    def health() -> dict:
        active = [job for job in manager.list() if job['state'] in ('queued', 'running',
                                                                    'cancel_requested')]
        return {'ok': True, **app_version.version_fields(), 'generation': {
            'available': True, 'busy': bool(active),
            'queued': sum(j['state'] == 'queued' for j in active),
            'running': sum(j['state'] in ('running', 'cancel_requested') for j in active),
            'runtime_revision': manager.runtime_sha256}}

    @app.get('/api/fleet/activity')
    def fleet_activity() -> dict:
        """The private activity contract the Hub polls after health."""
        return manager.activity_snapshot()

    def capabilities(model) -> list[str]:
        capability_for = {'image-to-video': 'img2video', 'text-to-video': 'txt2video'}
        return [capability_for[task] for task in model.tasks if task in capability_for]

    def catalog_entry(model) -> dict:
        """One registered model, described by its own registry entry and this host."""
        state = model_state(model)
        host = state['host']
        warning = memory_warning(model, host)
        files = state['files']
        params = lane_parameters(model)
        in_force = model_defaults.stated(params) if model.runnable else dict(model.defaults)
        sizes = [{'aspect_ratio': name, 'width': width, 'height': height,
                  'resolution': klass, 'tier': 'video',
                  'default': (name == model.defaults['aspect_ratio']
                              and klass == model.defaults['resolution'])}
                 for klass, canvases in sorted(CANVAS_CLASSES.items())
                 for name, (width, height) in sorted(canvases.items())]
        reason = None if state['ready'] else (
            model.unavailable_reason if not model.runnable
            else state.get('muxer_problem')
            or files.get('reason')
            or (None if state['tier_eligible'] else
                f'below the {model.qualified_minimum_memory_gb:g} GB tier this model is '
                f'qualified at')
            or host.get('reason'))
        return {
            'label': model.label, 'name': model.label, 'repo': model.repo,
            'model_id': model.repo, 'family': model.family, 'modality': 'video',
            'package_id': model.package_id,
            'capabilities': capabilities(model), 'hub_operation': 'video.generation',
            'model_revision': model.revision, 'qualified_revision': model.revision,
            'qualified_revision_match': True,
            'runtime_revision': manager.runtime_sha256,
            'runtime_compatible': True, 'runtime_ready': model.runnable,
            'memory_eligible': bool(state['tier_eligible'] and host['eligible']),
            'min_total_memory_gb': model.qualified_minimum_memory_gb,
            'min_unified_memory_gb': model.qualified_minimum_memory_gb,
            'qualified_minimum_memory_gb': model.qualified_minimum_memory_gb,
            'min_free_memory_gb': model.min_free_memory_gb,
            'memory_warning': warning,
            'memory_policy': {
                'low_memory': model.memory.low_memory,
                'low_ram_streaming': model.memory.low_ram_streaming,
                'relay_below_bytes': model.memory.relay_on_below_bytes,
                'total_memory_bytes': host.get('unified_memory_bytes'),
                'measured_peak_gb': (model.qualification.get('measured_runs') or [{}])[0]
                                    .get('peak_process_footprint_gb'),
            },
            'cache': {'state': ('cached' if files['state'] == 'files_verified'
                                else files['state'])},
            'readiness': {'ready': state['ready'], 'files': files, 'host': host,
                          'tier_eligible': state['tier_eligible'],
                          'reason': reason},
            'execution_ready': state['ready'], 'available': state['ready'],
            'generation_defaults': in_force,
            'generation_limits': {key: value for key, value in model.limits.items()
                                  if key != 'max_prompt'},
            'max_prompt_characters': model.limits['max_prompt'],
            'modes': list(model.tasks),
            'generates_audio': model.generates_audio,
            'video_sizes': sizes,
            'lane_contract': lane_contract(model, manager.data) if model.runnable else None,
            'qualification': dict(model.qualification),
            'requires_packages': list(model.requires),
        }

    def entry_without_a_runtime_model(manifest: dict) -> dict:
        """One published row for a Video package this runtime has no model for."""
        host = _host_facts()
        reason = missing_runtime_model_reason(manifest)
        return {
            'label': manifest.get('label') or manifest['package_id'],
            'name': manifest.get('label') or manifest['package_id'],
            'repo': manifest['model_id'], 'model_id': manifest['model_id'],
            'family': manifest.get('family'), 'modality': 'video',
            'engine': manifest['engine'], 'package_id': manifest['package_id'],
            'capabilities': [], 'hub_operation': 'video.generation',
            'model_revision': manifest['revision'],
            'memory_eligible': False,
            'cache': {'state': 'no_runtime_model'},
            'readiness': {'ready': False, 'state': 'no_runtime_model', 'reason': reason,
                          'files': {'state': 'no_runtime_model', 'reason': reason},
                          'host': host},
            'runtime_compatible': False, 'runtime_ready': False,
            'execution_ready': False, 'available': False,
        }

    @app.get('/api/catalog')
    def catalog() -> dict:
        entries = [catalog_entry(model) for model in MODELS.values()]
        entries.extend(entry_without_a_runtime_model(manifest)
                       for manifest in manager.packages_without_a_runtime_model())
        return {'models': entries}

    def _accept(raw: dict, image_path: str | None = None) -> dict:
        state = _host_facts()
        if not state['eligible']:
            raise HTTPException(409, state['reason'])
        body = dict(raw)
        if image_path:
            body['image_path'] = image_path
        try:
            return {'job': manager.submit(body)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post('/api/generate/txt2video')
    def submit_text_to_video(body: dict) -> dict:
        return _accept(body)

    def _store_upload(job_token: str, upload: UploadFile) -> str:
        uploads = manager.data / 'uploads'
        uploads.mkdir(parents=True, exist_ok=True, mode=0o700)
        suffix = Path(upload.filename or '').suffix.lower()
        if suffix not in ('.png', '.jpg', '.jpeg', '.webp'):
            suffix = '.png'
        target = uploads / f'{job_token}{suffix}'
        size = 0
        with target.open('wb') as stream:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_SOURCE_IMAGE_BYTES:
                    stream.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(413, f'source image must be at most '
                                             f'{MAX_SOURCE_IMAGE_BYTES // (1024 * 1024)} MB')
                stream.write(chunk)
        if size == 0:
            target.unlink(missing_ok=True)
            raise HTTPException(422, 'source image is empty')
        return str(target)

    @app.post('/api/generate/video2video')
    def submit_image_to_video(
        request: Request,
        file: UploadFile = File(...),
        repo: str = Form(default=''),
        mode: str = Form(default='img2video'),
        prompt: str = Form(default=''),
        width: str | None = Form(default=None),
        height: str | None = Form(default=None),
        frames: str | None = Form(default=None),
        fps: str | None = Form(default=None),
        duration: str | None = Form(default=None),
        resolution: str | None = Form(default=None),
        aspect_ratio: str | None = Form(default=None),
        seed: str | None = Form(default=None),
    ) -> dict:
        """The Hub's image-to-video door: multipart ``file`` plus flattened form fields."""
        staged = _store_upload(uuid4().hex, file)
        raw: dict = {'repo': repo or MODEL_ID, 'mode': mode, 'prompt': prompt,
                     'image_path': staged}
        for key, value in (('width', width), ('height', height), ('frames', frames),
                           ('fps', fps), ('duration', duration), ('resolution', resolution),
                           ('aspect_ratio', aspect_ratio), ('seed', seed)):
            if value not in (None, ''):
                raw[key] = value
        try:
            return _accept(raw)
        finally:
            # The staged copy belongs to the job the manager accepted (it validated the path
            # and the runner reads it); a request that never reached the manager leaves no file.
            if raw.get('image_path') and not any(
                    job.get('params', {}).get('image_path') == staged
                    for job in manager.list()):
                Path(staged).unlink(missing_ok=True)

    @app.get('/api/generate/jobs')
    def jobs() -> dict:
        return {'jobs': manager.list()}

    @app.get('/api/generate/jobs/{job_id}')
    def job(job_id: str) -> dict:
        value = manager.get(job_id)
        if value is None:
            raise HTTPException(404, 'job not found')
        return {'job': value}

    @app.delete('/api/generate/jobs/{job_id}')
    def cancel(job_id: str) -> dict:
        value = manager.cancel(job_id)
        if value is None:
            raise HTTPException(404, 'job not found')
        return {'job': value}

    @app.get('/api/generate/jobs/{job_id}/video')
    def video(job_id: str):
        value = manager.get(job_id)
        if value is None or value['state'] != 'done':
            raise HTTPException(404, 'final MP4 is unavailable')
        path = manager.outputs / f'{job_id}.mp4'
        if not final_matches(value, path):
            raise HTTPException(409, 'final MP4 changed after generation')
        return FileResponse(path, media_type='video/mp4',
                            headers={'X-Content-SHA256': value['sha256']})

    @app.post('/api/generate/jobs/{job_id}/ack')
    def ack(job_id: str) -> dict:
        receipt = manager.ack(job_id)
        if receipt is None:
            raise HTTPException(404, 'completed Video job not found')
        return receipt

    # ── model memory policy ────────────────────────────────────────────────
    @app.get('/api/memory-policy')
    def memory_policy_status() -> dict:
        return memory_policy.status(manager)

    @app.put('/api/memory-policy')
    def memory_policy_save(body: dict | None = None) -> dict:
        mode = str((body or {}).get('mode') or '')
        if mode != 'immediate':
            raise HTTPException(422, 'Video releases its pipeline after every job; '
                                     'there is no idle policy to change')
        return memory_policy.status(manager)

    @app.post('/api/memory/release')
    def memory_release() -> dict:
        try:
            return memory_policy.release(manager)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    # ── per-model generation defaults ───────────────────────────────────────
    def model_defaults_status() -> dict:
        return model_defaults.status(
            manager.data,
            [(model.repo, lane_parameters(model)) for model in MODELS.values()
             if model.runnable])

    def registered_model(repo: object):
        model = MODELS.get(repo) if isinstance(repo, str) else None
        if model is None or not model.runnable:
            raise HTTPException(
                422, f'{repo!r} is not a runnable video model; this worker serves: '
                     + ', '.join(sorted(name for name, item in MODELS.items() if item.runnable)))
        return model

    @app.get('/api/model-defaults')
    def get_model_defaults() -> dict:
        return model_defaults_status()

    @app.put('/api/model-defaults')
    def put_model_defaults(body: dict | None = None) -> dict:
        values = (body or {}).get('values')
        if not isinstance(values, dict):
            raise HTTPException(422, 'values must be an object of parameter to default')
        model = registered_model((body or {}).get('repo'))
        try:
            model_defaults.save(manager.data, model.repo, values, lane_parameters(model))
        except ValueError as exc:
            raise HTTPException(422, detail=model_defaults.refusals(exc) or str(exc)) from exc
        return model_defaults_status()

    @app.delete('/api/model-defaults')
    def delete_model_defaults(body: dict | None = None, repo: str | None = None) -> dict:
        model = registered_model((body or {}).get('repo') or repo)
        model_defaults.clear(manager.data, model.repo)
        return model_defaults_status()

    return app


def from_environment() -> FastAPI:
    from components import model_packages
    from .generation import load_video_manifests

    data = Path(os.environ['STUDIOHUB_VIDEO_DATA_DIR'])
    library = Path(os.environ['STUDIOHUB_MODELS_DIR'])
    manifest_path = Path(os.environ['STUDIOHUB_VIDEO_MANIFEST'])
    manifest = json.loads(manifest_path.read_text())
    model_packages.validate_manifest(manifest)
    manifests, errors = load_video_manifests(manifest_path.parent, manifest_path)
    release = json.loads(Path(os.environ['STUDIOHUB_RELEASE_MANIFEST']).read_text())
    identity = release['components']['video']['runtime_sha256']
    if not isinstance(identity, str) or len(identity) != 64:
        raise ValueError('Release manifest lacks exact Video runtime identity')
    return create_app(GenerationManager(data, library, manifest, identity,
                                        manifests=manifests, manifest_errors=errors),
                      os.environ['STUDIOHUB_FLEET_TOKEN'])
