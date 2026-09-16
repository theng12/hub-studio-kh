"""Private standalone Image API for one pinned, qualified FLUX.2 Klein mode."""
from __future__ import annotations

import hmac
import os
from pathlib import Path
import platform
import subprocess

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from . import memory_policy
from .generation import (DEFAULTS, MODEL_ID, MODEL_REVISION, SIZE_PRESETS,
                         GenerationManager, final_matches)


def _host_eligibility() -> dict:
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
    if memory < 16_000_000_000:
        return {'eligible': False, 'reason': 'This FLUX operation requires 16 GB unified memory'}
    return {'eligible': True, 'macos': version, 'unified_memory_bytes': memory,
            'qualification': '16 GB 1K txt2img historical source receipt; this host still needs packaged proof'}


def create_app(manager: GenerationManager, token: str) -> FastAPI:
    if not token:
        raise ValueError('Private Image worker requires its owning Hub credential')
    app = FastAPI(title='Studio Hub Image', version='1')

    @app.middleware('http')
    async def require_owner(request: Request, call_next):
        offered = (request.headers.get('x-studio-token') or request.headers.get('x-hub-token')
                   or request.headers.get('authorization', '').removeprefix('Bearer '))
        if not hmac.compare_digest(offered, token):
            return JSONResponse({'detail': 'Owner credential required'}, status_code=401)
        return await call_next(request)

    def readiness() -> dict:
        files = manager.preflight()
        host = _host_eligibility()
        ready = files['state'] == 'files_verified' and host['eligible']
        return {'ready': ready, 'files': files, 'host': host,
                'runtime_identity': {'id': manager.runtime_sha256, 'compatible': True}}

    @app.get('/api/health')
    def health() -> dict:
        active = [job for job in manager.list() if job['state'] in ('queued', 'running')]
        return {'ok': True, 'app_version': 'standalone', 'generation': {
            'available': True, 'busy': bool(active), 'queued': sum(j['state']=='queued' for j in active),
            'running': sum(j['state']=='running' for j in active),
            'runtime_revision': manager.runtime_sha256}}

    @app.get('/api/catalog')
    def catalog() -> dict:
        state = readiness()
        default_width, default_height = SIZE_PRESETS[DEFAULTS['aspect_ratio']]
        sizes = [{'id': ratio, 'label': ratio, 'width': width, 'height': height}
                 for ratio, (width, height) in SIZE_PRESETS.items()]
        ready = state['ready']
        files = state['files']
        return {'models': [{
            'label': 'FLUX.2 Klein', 'name': 'FLUX.2 Klein', 'repo': MODEL_ID,
            'model_id': MODEL_ID, 'family': 'flux2-klein', 'modality': 'image',
            'capabilities': ['txt2img'], 'hub_operation': 'image.text_to_image',
            'model_revision': MODEL_REVISION, 'qualified_revision': MODEL_REVISION,
            'qualified_revision_match': True, 'runtime_revision': manager.runtime_sha256,
            'runtime_compatible': True, 'runtime_ready': True,
            'memory_eligible': state['host']['eligible'], 'min_total_memory_gb': 16,
            'cache': {'state': 'cached' if files['state'] == 'files_verified' else files['state']},
            'readiness': {'ready': ready, 'files': files, 'host': state['host'],
                          'reason': None if ready else files.get('reason') or state['host'].get('reason')},
            'execution_ready': ready, 'available': ready,
            'generation_defaults': {**DEFAULTS, 'width': default_width, 'height': default_height},
            'image_sizes': sizes,
            'generation_limits': {'min_steps': 2, 'max_steps': 100,
                                  'min_guidance': 0.0, 'max_guidance': 30.0,
                                  'min_seed': -1, 'max_seed': 2**32-1},
            'sizes': [{'aspect_ratio': item['id'], 'width': item['width'],
                       'height': item['height'], 'tier': '1K',
                       'default': item['id'] == DEFAULTS['aspect_ratio']} for item in sizes],
            'qualification': {'historical_source_workload': '1K, four steps, guidance 1.0 on 16 GB Apple Silicon',
                              'packaged_inference': 'pending'},
        }]}

    @app.post('/api/generate/txt2img')
    def submit(body: dict) -> dict:
        state = readiness()
        if not state['host']['eligible']:
            raise HTTPException(409, state['host']['reason'])
        try:
            return {'job': manager.submit(body)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

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

    @app.get('/api/generate/jobs/{job_id}/image')
    def image(job_id: str):
        value = manager.get(job_id)
        if value is None or value['state'] != 'done':
            raise HTTPException(404, 'final PNG is unavailable')
        path = manager.outputs / f'{job_id}.png'
        if not final_matches(value, path):
            raise HTTPException(409, 'final PNG changed after generation')
        return FileResponse(path, media_type='image/png',
                            headers={'X-Content-SHA256': value['sha256']})

    @app.post('/api/generate/jobs/{job_id}/ack')
    def ack(job_id: str) -> dict:
        receipt = manager.ack(job_id)
        if receipt is None:
            raise HTTPException(404, 'completed Image job not found')
        return receipt

    # ── model memory policy ────────────────────────────────────────────────
    # The fleet contract every Studio publishes. Image has no idle timer of its own — mflux
    # is built inside the job and dropped when it ends — but the Hub's machine-wide memory
    # inventory and handoff pass read all Studios through this shape.

    @app.get('/api/memory-policy')
    def memory_policy_status() -> dict:
        return memory_policy.status(manager)

    @app.put('/api/memory-policy')
    def memory_policy_save(body: dict | None = None) -> dict:
        mode = str((body or {}).get('mode') or '')
        if mode != 'immediate':
            raise HTTPException(422, 'Image releases its model after every job; '
                                     'there is no idle policy to change')
        return memory_policy.status(manager)

    @app.post('/api/memory/release')
    def memory_release() -> dict:
        try:
            return memory_policy.release(manager)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    return app


def from_environment() -> FastAPI:
    from components import model_packages
    import json
    data = Path(os.environ['STUDIOHUB_IMAGE_DATA_DIR'])
    library = Path(os.environ['STUDIOHUB_MODELS_DIR'])
    manifest_path = Path(os.environ['STUDIOHUB_IMAGE_MANIFEST'])
    manifest = json.loads(manifest_path.read_text())
    model_packages.validate_manifest(manifest)
    release = json.loads(Path(os.environ['STUDIOHUB_RELEASE_MANIFEST']).read_text())
    identity = release['components']['image']['runtime_sha256']
    if not isinstance(identity, str) or len(identity) != 64:
        raise ValueError('Release manifest lacks exact Image runtime identity')
    return create_app(GenerationManager(data, library, manifest, identity),
                      os.environ['STUDIOHUB_FLEET_TOKEN'])
