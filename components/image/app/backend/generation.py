"""The source Image Studio's pinned FLUX.2 Klein path, scoped to 1K txt2img.

Adopted from imagestudio-mac 36f88a0bc0abc07e3d7a8cf04424ab5336501827,
app/backend/generation.py:_generate_flux2_klein and _publish_final_png.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
import os
from pathlib import Path
import random
import resource
import threading
import time
from uuid import uuid4

from PIL import Image


MODEL_ID = 'AITRADER/FLUX2-klein-4B-mlx-4bit'
MODEL_REVISION = '7fd24828501390b67a92c8b66d2fc5a707d0ba1a'
PACKAGE_ID = 'flux2-klein-4b-mlx-4bit'
SIZE_PRESETS = {
    '16:9': (1280, 720), '9:16': (720, 1280), '1:1': (1024, 1024),
    '4:3': (1152, 864), '3:4': (864, 1152), '3:2': (1200, 800),
    '2:3': (800, 1200), '5:4': (1120, 896), '4:5': (896, 1120),
    '21:9': (1680, 720), '2:1': (1408, 704), '1:2': (704, 1408),
}
DEFAULTS = {'aspect_ratio': '16:9', 'resolution': '1K', 'steps': 4,
            'guidance': 1.0, 'seed': -1}
TERMINAL = {'done', 'error', 'cancelled'}


def validate_request(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError('Image request must be an object')
    if raw.get('repo') != MODEL_ID or raw.get('model_revision', MODEL_REVISION) != MODEL_REVISION:
        raise ValueError('Only the pinned FLUX.2 Klein 4-bit model is available')
    if any(raw.get(key) for key in ('reference_images', 'image_path', 'lora_names',
                                     'lora_paths', 'negative_prompt', 'quantize')):
        raise ValueError('Reference images, LoRAs, negative prompt and quantization are unavailable')
    prompt = raw.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        raise ValueError('Prompt must contain 1–4000 characters')
    ratio = raw.get('aspect_ratio')
    if ratio is None:
        dimensions = (raw.get('width'), raw.get('height'))
        if dimensions == (None, None):
            ratio = DEFAULTS['aspect_ratio']
        else:
            ratio = next((name for name, pixels in SIZE_PRESETS.items()
                          if dimensions == pixels), None)
    if ratio not in SIZE_PRESETS or raw.get('resolution', '1K') != '1K':
        raise ValueError('Choose a qualified 1K aspect ratio')
    width, height = SIZE_PRESETS[ratio]
    if type(raw.get('width', width)) is not int or type(raw.get('height', height)) is not int:
        raise ValueError('Width and height must be integer 1K preset pixels')
    if (raw.get('width', width), raw.get('height', height)) != (width, height):
        raise ValueError('Dimensions must match the selected 1K aspect ratio')
    steps = raw.get('steps', 4)
    guidance = raw.get('guidance', 1.0)
    if type(steps) is not int or not 2 <= steps <= 100:
        raise ValueError('Steps must be between 2 and 100')
    if (type(guidance) not in (int, float) or not math.isfinite(guidance)
            or not 0 <= guidance <= 30):
        raise ValueError('Guidance must be finite and between 0 and 30')
    seed = raw.get('seed', -1)
    if seed is None:
        seed = -1
    if type(seed) is not int or not -1 <= seed <= 2**32 - 1:
        raise ValueError('Seed must be -1 or a 32-bit unsigned integer')
    return {'repo': MODEL_ID, 'model_revision': MODEL_REVISION, 'prompt': prompt.strip(),
            'aspect_ratio': ratio, 'resolution': '1K', 'width': width, 'height': height,
            'steps': steps, 'guidance': float(guidance), 'seed': seed}


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def final_matches(job: dict, path: Path) -> bool:
    try:
        return bool(not path.is_symlink() and path.is_file()
                    and job.get('output_path') == str(path)
                    and path.stat().st_size == job.get('bytes')
                    and sha256(path.read_bytes()).hexdigest() == job.get('sha256'))
    except OSError:
        return False


class GenerationManager:
    def __init__(self, data: Path, model_library: Path, manifest: dict,
                 runtime_sha256: str, machine: str = 'local') -> None:
        self.data = data
        self.outputs = data / 'outputs'
        self.outputs.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.outputs.is_symlink():
            raise ValueError('Image output directory cannot be a symlink')
        self.state_file = data / 'jobs.json'
        self.library = model_library
        self.manifest = manifest
        self.runtime_sha256 = runtime_sha256
        self.machine = machine
        self.jobs: dict[str, dict] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.lock = threading.RLock()
        self.inference_lock = threading.Lock()
        self._restore()

    def _restore(self) -> None:
        try:
            saved = json.loads(self.state_file.read_text())
            if not isinstance(saved, dict):
                raise ValueError('Image job history is malformed')
        except FileNotFoundError:
            saved = {}
        for job_id, job in saved.items():
            if not isinstance(job, dict) or job_id != job.get('id'):
                continue
            if job.get('state') not in TERMINAL:
                uncertain_output = (self.outputs / f'{job_id}.png').exists()
                job.update(state='error',
                    error=('Final PNG exists without a durable completion receipt; reconcile before retry'
                           if uncertain_output else 'Worker restarted before completion'),
                    error_code=('JOB_PERSISTENCE_UNCERTAIN' if uncertain_output
                                else 'WORKER_RESTART_UNCERTAIN'), finished_at=time.time())
            if job.get('state') == 'done':
                path = self.outputs / f'{job_id}.png'
                if not final_matches(job, path):
                    job.update(state='error', error='Final PNG is unavailable after restart',
                               error_code='OUTPUT_UNAVAILABLE', output_path=None, output_url=None)
            self.jobs[job_id] = job
        self._persist()

    def _persist(self) -> None:
        _atomic_json(self.state_file, self.jobs)

    def preflight(self) -> dict:
        from components import model_packages
        return model_packages.preflight(self.manifest, self.library)

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            return dict(self.jobs[job_id]) if job_id in self.jobs else None

    def list(self) -> list[dict]:
        with self.lock:
            return [dict(job) for job in sorted(self.jobs.values(),
                    key=lambda value: value['created_at'], reverse=True)]

    def submit(self, raw: dict) -> dict:
        params = validate_request(raw)
        files = self.preflight()
        if files['state'] != 'files_verified':
            raise RuntimeError(f'MODEL_FILES_UNAVAILABLE: {files.get("reason") or files["state"]}')
        job_id = uuid4().hex
        job = {'id': job_id, 'mode': 'txt2img', 'params': params, 'state': 'queued',
               'progress': 0.0, 'created_at': time.time(), 'started_at': None,
               'finished_at': None, 'generated_at': None, 'duration_seconds': None,
               'output_path': None, 'output_url': None, 'sha256': None, 'bytes': None,
               'width': None, 'height': None, 'steps': None, 'resolved_seed': None,
               'media_type': None, 'error': None, 'error_code': None,
               'model_revision': MODEL_REVISION, 'runtime_revision': self.runtime_sha256,
               'worker_id': 'image', 'machine_id': self.machine, 'acknowledged': False}
        with self.lock:
            self.jobs[job_id] = job
            self.cancel_events[job_id] = threading.Event()
            try:
                self._persist()
            except Exception:
                self.jobs.pop(job_id, None)
                self.cancel_events.pop(job_id, None)
                raise
        threading.Thread(target=self._run, args=(job_id, files['folder']), daemon=True,
                         name=f'image-{job_id[:8]}').start()
        return dict(job)

    def cancel(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job['state'] not in TERMINAL:
                self.cancel_events[job_id].set()
                if job['state'] == 'queued':
                    job.update(state='cancelled', finished_at=time.time())
                    self._persist()
            return dict(job)

    def ack(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job['state'] != 'done':
                return None
            if not final_matches(job, self.outputs / f'{job_id}.png'):
                return None
            if not job['acknowledged']:
                job['acknowledged'] = True
                job['receipt_acked_at'] = time.time()
                try:
                    self._persist()
                except Exception:
                    job['acknowledged'] = False
                    job.pop('receipt_acked_at', None)
                    raise
            return {'ok': True, 'job_id': job_id, 'sha256': job['sha256']}

    def _run(self, job_id: str, model_folder: str) -> None:
        staged = self.outputs / f'.{job_id}.png'
        final = self.outputs / f'{job_id}.png'
        with self.inference_lock:
            with self.lock:
                event = self.cancel_events[job_id]
                job = self.jobs[job_id]
                if event.is_set():
                    job.update(state='cancelled', finished_at=time.time())
                    self._persist()
                    return
                job.update(state='running', progress=0.05, started_at=time.time())
                try:
                    self._persist()
                except Exception:
                    # No inference has started. Never leave an in-memory job
                    # looking active after its running receipt failed.
                    job.update(state='error', error='Running state could not be persisted',
                               error_code='JOB_PERSISTENCE_FAILED', finished_at=time.time())
                    try:
                        self._persist()
                    except Exception:
                        pass  # restored queued receipt becomes restart uncertainty
                    self.cancel_events.pop(job_id, None)
                    return
            try:
                files = self.preflight()
                if files['state'] != 'files_verified' or files['folder'] != model_folder:
                    raise RuntimeError('MODEL_FILES_UNAVAILABLE: pinned model changed before inference')
                staged.unlink(missing_ok=True)
                seed = job['params']['seed']
                if seed == -1:
                    seed = random.randint(0, 2**32 - 1)
                with self.lock:
                    job['resolved_seed'] = seed
                    self._persist()
                # Pinned mflux 0.17.5 path from the exact Image source commit.
                from mflux.models.flux2.variants.txt2img.flux2_klein import Flux2Klein
                # ST-01's verified package adds one model/ prefix; mflux reads
                # the original publisher snapshot hierarchy inside it.
                flux = Flux2Klein(model_path=str(Path(model_folder) / 'model'), quantize=None,
                                  lora_paths=None, lora_scales=None)
                result = flux.generate_image(seed=seed, prompt=job['params']['prompt'],
                    num_inference_steps=job['params']['steps'], height=job['params']['height'],
                    width=job['params']['width'], guidance=job['params']['guidance'])
                if not hasattr(result, 'save'):
                    raise RuntimeError('mflux did not return a GeneratedImage')
                result.save(str(staged), overwrite=True)
                with Image.open(staged) as image:
                    if image.format != 'PNG':
                        raise RuntimeError('mflux did not produce a PNG')
                    image.verify()
                with Image.open(staged) as image:
                    dimensions = image.size
                expected = (job['params']['width'], job['params']['height'])
                if dimensions != expected:
                    raise RuntimeError(f'Generated dimensions {dimensions} differ from requested {expected}')
                content = staged.read_bytes()
                if not content.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise RuntimeError('mflux PNG signature is invalid')
                with self.lock:
                    if event.is_set():
                        job.update(state='cancelled', finished_at=time.time())
                    else:
                        os.replace(staged, final)
                        now = time.time()
                        job.update(state='done', progress=1.0, finished_at=now,
                            generated_at=now, duration_seconds=now-job['started_at'],
                            output_path=str(final),
                            output_url=f'/api/generate/jobs/{job_id}/image',
                            sha256=sha256(content).hexdigest(), bytes=len(content),
                            media_type='image/png', width=dimensions[0],
                            height=dimensions[1], steps=job['params']['steps'],
                            peak_process_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
                    try:
                        self._persist()
                    except Exception:
                        job.update(state='error', output_path=None, output_url=None,
                                   sha256=None, bytes=None, media_type=None,
                                   error='Final output could not be persisted; reconcile before retry',
                                   error_code='JOB_PERSISTENCE_UNCERTAIN')
                        try:
                            final.unlink(missing_ok=True)
                            job.update(error='Final output could not be persisted; generation was discarded',
                                       error_code='JOB_PERSISTENCE_FAILED')
                        except OSError:
                            pass
                        raise
            except Exception as exc:
                with self.lock:
                    if event.is_set():
                        job.update(state='cancelled', error=None, error_code=None)
                    else:
                        detail = str(exc)
                        if job.get('error_code') not in ('JOB_PERSISTENCE_FAILED',
                                                         'JOB_PERSISTENCE_UNCERTAIN'):
                            job.update(state='error', error=detail[:500],
                                       error_code=('MODEL_FILES_UNAVAILABLE' if 'MODEL_FILES_UNAVAILABLE' in detail
                                                   else 'IMAGE_GENERATION_FAILED'))
                    job['finished_at'] = time.time()
                    job['duration_seconds'] = job['finished_at'] - job['started_at']
                    self._persist()
            finally:
                staged.unlink(missing_ok=True)
                self.cancel_events.pop(job_id, None)
