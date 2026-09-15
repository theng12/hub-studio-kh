"""Independent Subtitle job lifecycle: submission, cancellation, history.

Public Subtitle studio state lives here and nowhere else. It is deliberately a
separate ledger from the Voice service's jobs and from the Hub's transcription
batches: two processes, two state files, two artifact trees, one shared
immutable model/runtime asset.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import threading
import time
from uuid import uuid4

from .engine import ModelPackageError, SubtitleEngine, _Cancelled

TERMINAL = {'done', 'error', 'cancelled'}
MAX_INPUT_BYTES = 2 * 1024 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
ARTIFACT_FORMATS = {
    'srt': ('application/x-subrip', 'srt'),
    'vtt': ('text/vtt', 'vtt'),
    'text': ('text/plain; charset=utf-8', 'txt'),
}
ALLOWED_EXTENSIONS = {
    '.wav', '.mp3', '.m4a', '.aac', '.flac', '.ogg', '.opus',
    '.webm', '.mp4', '.mov',
}
_LANGUAGE = re.compile(r'^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$')


def _atomic_json(path: Path, value) -> None:
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


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = sha256()
    total = 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b''):
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def safe_input_name(value: str) -> str:
    """Accept an ordinary upload filename without ever trusting it as a path."""
    name = (value or '').strip()
    if (not name or len(name) > 240 or Path(name).name != name or '/' in name
            or '\\' in name or '\x00' in name
            or any(ord(char) < 32 for char in name)):
        raise ValueError('invalid filename')
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(f'unsupported audio/video type: {suffix or "none"}')
    return name


def validate_request(raw: dict, *, packages: list[str], default_package: str,
                     aliases: dict[str, str] | None = None) -> dict:
    if not isinstance(raw, dict):
        raise ValueError('Subtitle request must be an object')
    model = str(raw.get('model') or default_package).strip()
    # The Hub's transcription contract sends the model repository
    # (mlx-community/whisper-large-v3-turbo); this worker's own catalogue keys on the package
    # id (whisper-turbo). Accept either spelling rather than refusing a valid packaged model.
    if aliases and model in aliases:
        model = aliases[model]
    if model not in packages:
        raise ValueError('choose one of the packaged Whisper models')
    language = raw.get('language')
    if language is not None and str(language).strip():
        language = str(language).strip()
        if not _LANGUAGE.fullmatch(language):
            raise ValueError('language must be a short BCP-47 code such as "en"')
    else:
        language = None
    word_timestamps = raw.get('word_timestamps', False)
    if not isinstance(word_timestamps, bool):
        raise ValueError('word_timestamps must be a boolean')
    formats = raw.get('formats')
    if formats is None:
        formats = ['srt', 'vtt', 'text']
    if (not isinstance(formats, list) or not formats
            or any(not isinstance(item, str) or item not in ARTIFACT_FORMATS
                   for item in formats)):
        raise ValueError(f'formats must be a non-empty list drawn from {sorted(ARTIFACT_FORMATS)}')
    if 'srt' not in formats:
        # The Hub's transcription contract reads SRT from the response and the
        # delivery receipt is the SRT artifact, so SRT is always published.
        formats = [*formats, 'srt']
    return {'model': model, 'language': language,
            'word_timestamps': word_timestamps, 'formats': sorted(set(formats))}


class SubtitleManager:
    def __init__(self, data: Path, library: Path, manifests: list[dict],
                 runtime_sha256: str, *, voice_app_dir: Path,
                 machine: str = 'local', offline: bool = True) -> None:
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.data.is_symlink():
            raise ValueError('Subtitle state directory cannot be a symlink')
        self.inputs = self.data / 'inputs'
        self.artifacts = self.data / 'artifacts'
        for directory in (self.inputs, self.artifacts):
            directory.mkdir(exist_ok=True, mode=0o700)
            if directory.is_symlink():
                raise ValueError('Subtitle state subdirectories cannot be symlinks')
        self.state_file = self.data / 'jobs.json'
        self.machine = machine
        self.engine = SubtitleEngine(library, manifests, runtime_sha256,
                                     voice_app_dir=voice_app_dir, offline=offline,
                                     processor_cache=self.data / 'processor')
        self.jobs: dict[str, dict] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.lock = threading.RLock()
        self._restore()

    # ── persistence ────────────────────────────────────────────────────────

    def _persist(self) -> None:
        try:
            _atomic_json(self.state_file, self.jobs)
        except FileNotFoundError:
            # The owning state directory disappeared underneath this process.
            # Never raise from a durability write on a worker thread.
            return

    def _restore(self) -> None:
        try:
            saved = json.loads(self.state_file.read_text())
            if not isinstance(saved, dict):
                raise ValueError('Subtitle job history is malformed')
        except FileNotFoundError:
            saved = {}
        for job_id, job in saved.items():
            if not isinstance(job, dict) or job_id != job.get('id'):
                continue
            if job.get('state') not in TERMINAL:
                uncertain = any(self.artifacts.glob(f'{job_id}.*'))
                job.update(
                    state='error', finished_at=time.time(),
                    error=('Subtitle artifacts exist without a durable completion receipt; '
                           'reconcile before retry' if uncertain
                           else 'Worker restarted before completion'),
                    error_code=('JOB_PERSISTENCE_UNCERTAIN' if uncertain
                                else 'WORKER_RESTART_UNCERTAIN'))
            elif job.get('state') == 'done':
                if not self._final_matches(job):
                    job.update(state='error', error='Subtitle artifact is unavailable after restart',
                               error_code='OUTPUT_UNAVAILABLE', artifacts={},
                               output_url=None, sha256=None, bytes=None)
            self.jobs[job_id] = job
        self._persist()

    def _final_matches(self, job: dict) -> bool:
        """Every published artifact must still match its recorded bytes/hash."""
        artifacts = job.get('artifacts') or {}
        if not artifacts:
            return False
        for fmt, receipt in artifacts.items():
            suffix = ARTIFACT_FORMATS[fmt][1]
            path = self.artifacts / f"{job['id']}.{suffix}"
            try:
                if (path.is_symlink() or not path.is_file()
                        or receipt.get('path') != str(path)
                        or path.stat().st_size != receipt.get('bytes')
                        or sha256(path.read_bytes()).hexdigest() != receipt.get('sha256')):
                    return False
            except OSError:
                return False
        return True

    # ── catalog / readiness ────────────────────────────────────────────────

    def catalog(self) -> list[dict]:
        return self.engine.catalog()

    def default_package(self) -> str:
        return self.engine.default_package()

    def model_aliases(self) -> dict[str, str]:
        """Map each model repository to its package id.

        The Hub's transcription contract identifies a model by repository, while this worker
        keys its catalogue by package id; accepting both keeps the existing Hub contract and
        the retained Voice-shaped endpoint working without a translation layer.
        """
        return {
            manifest['model_id']: package_id
            for package_id, manifest in self.engine.manifests.items()
            if manifest.get('model_id')
        }

    def readiness(self, package_id: str) -> dict:
        return self.engine.readiness(package_id)

    def availability(self) -> dict:
        """Voice-compatible ``/api/transcribe/availability`` shape."""
        models = []
        for entry in self.catalog():
            files = entry['readiness']['files']
            models.append({
                'repo': entry['repo'], 'package_id': entry['package_id'],
                'label': entry['label'], 'engine': entry['engine'],
                'model_revision': entry['model_revision'],
                'languages': entry['languages'],
                'supports_segment_timestamps': True,
                'supports_word_timestamps': True,
                'supports_long_form': True,
                'recommended': entry['recommended'],
                'cached': files['state'] == 'files_verified',
                'ready': entry['readiness']['ready'],
                'reason': entry['readiness']['reason'],
                'cache': {'state': files['state'],
                          'snapshot_revision': entry['model_revision'],
                          'path': files.get('folder')},
                'memory_eligible': entry['readiness']['host']['eligible'],
            })
        return {'available': any(model['ready'] for model in models),
                'device': 'metal',
                'default_model': self.default_package(),
                'models': models,
                'export_source': self.engine.export_source()}

    # ── job lifecycle ──────────────────────────────────────────────────────

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            return dict(self.jobs[job_id]) if job_id in self.jobs else None

    def list(self) -> list[dict]:
        with self.lock:
            return [dict(job) for job in sorted(
                self.jobs.values(), key=lambda value: value['created_at'], reverse=True)]

    def store_input(self, label: str, filename: str, data: bytes) -> dict:
        """Persist one upload under a generated path; the original is preserved."""
        name = safe_input_name(filename)
        if not data:
            raise ValueError('uploaded audio is empty')
        if len(data) > MAX_INPUT_BYTES:
            raise ValueError('upload exceeds the per-file limit')
        token = re.sub(r'[^A-Za-z0-9._-]', '', str(label or ''))[:32]
        directory = self.inputs / f'{token or "upload"}-{uuid4().hex[:12]}'
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        path = directory / f'original{Path(name).suffix.lower()}'
        temporary = directory / '.upload.part'
        temporary.write_bytes(data)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        digest, size = _sha256_file(path)
        return {'filename': name, 'path': str(path), 'bytes': size,
                'sha256': digest, 'media_type': 'application/octet-stream'}

    def submit(self, request: dict, source: dict, *, activity_id: str | None = None,
               original_path: str | None = None) -> dict:
        """Create one durable job. ``source`` comes from ``store_input``."""
        params = validate_request(
            request, packages=sorted(self.engine.manifests),
            default_package=self.default_package(),
            aliases=self.model_aliases())
        readiness = self.readiness(params['model'])
        if not readiness['ready']:
            raise ModelPackageError(
                readiness['reason'] or f'{params["model"]} is not ready')
        job_id = uuid4().hex
        now = time.time()
        job = {
            'id': job_id, 'state': 'queued', 'progress': 0.0,
            'created_at': now, 'started_at': None, 'finished_at': None,
            'generated_at': None, 'duration_seconds': None,
            'params': params, 'model': self.engine.manifests[params['model']]['model_id'],
            'package_id': params['model'],
            'model_revision': self.engine.manifests[params['model']]['revision'],
            'runtime_revision': self.engine.runtime_sha256,
            'input': {'filename': source['filename'], 'bytes': source['bytes'],
                      'sha256': source['sha256'],
                      'original_path': original_path or source['path']},
            'output_path': None, 'output_url': None, 'sha256': None, 'bytes': None,
            'artifacts': {}, 'text': None, 'language': None,
            'media_duration_seconds': None, 'segment_count': None,
            'activity_id': activity_id, 'error': None, 'error_code': None,
            'worker_id': 'subtitle', 'machine_id': self.machine,
            'acknowledged': False,
        }
        with self.lock:
            self.jobs[job_id] = job
            self.cancel_events[job_id] = threading.Event()
            try:
                self._persist()
            except Exception:
                self.jobs.pop(job_id, None)
                self.cancel_events.pop(job_id, None)
                raise
        threading.Thread(target=self._run, args=(job_id, source['path']), daemon=True,
                         name=f'subtitle-{job_id[:8]}').start()
        return dict(job)

    def cancel(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job['state'] not in TERMINAL:
                self.cancel_events[job_id].set()
                if job['state'] == 'queued':
                    job.update(state='cancelled', finished_at=time.time(),
                               error='cancelled before decoding')
                    self._persist()
            return dict(job)

    def ack(self, job_id: str) -> dict | None:
        """Idempotent verified-delivery receipt for the Hub ACK contract."""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job['state'] != 'done' or not self._final_matches(job):
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
            return {'ok': True, 'job_id': job_id, 'sha256': job['sha256'],
                    'bytes': job['bytes'], 'media_type': 'application/x-subrip'}

    def artifact_path(self, job_id: str, fmt: str) -> tuple[Path, str, str] | None:
        """Return (path, media_type, download suffix) for a finished job."""
        if fmt not in ARTIFACT_FORMATS:
            return None
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job['state'] != 'done':
                return None
            receipt = (job.get('artifacts') or {}).get(fmt)
        if not receipt:
            return None
        path = Path(str(receipt.get('path') or ''))
        try:
            if not path.is_file() or path.is_symlink():
                return None
            if path.stat().st_size != receipt.get('bytes'):
                return None
            if sha256(path.read_bytes()).hexdigest() != receipt.get('sha256'):
                return None
        except OSError:
            return None
        media_type, suffix = ARTIFACT_FORMATS[fmt]
        return path, media_type, suffix

    # ── decoding ───────────────────────────────────────────────────────────

    def _run(self, job_id: str, input_path: str) -> None:
        with self.lock:
            event = self.cancel_events[job_id]
            job = self.jobs[job_id]
            if event.is_set():
                job.update(state='cancelled', finished_at=time.time())
                self._persist()
                self.cancel_events.pop(job_id, None)
                return
            job.update(state='running', progress=0.05, started_at=time.time())
            try:
                self._persist()
            except Exception:
                job.update(state='error', error='Running state could not be persisted',
                           error_code='JOB_PERSISTENCE_FAILED', finished_at=time.time())
                try:
                    self._persist()
                except Exception:
                    pass
                self.cancel_events.pop(job_id, None)
                return
        params = job['params']
        staged: list[Path] = []
        try:
            if not Path(input_path).is_file():
                raise FileNotFoundError('uploaded audio is missing')
            result = self.engine.transcribe(
                input_path, package_id=params['model'],
                language=params['language'], word_timestamps=params['word_timestamps'],
                cancelled=event.is_set,
                progress=lambda value: self._progress(job_id, value))
            if event.is_set():
                raise _Cancelled()
            receipts = self._publish(job_id, result, params['formats'], staged)
            with self.lock:
                now = time.time()
                job.update(
                    state='done', progress=1.0, finished_at=now, generated_at=now,
                    duration_seconds=round(now - job['started_at'], 3),
                    output_path=receipts['srt']['path'] if 'srt' in receipts else
                    next(iter(receipts.values()))['path'],
                    output_url=f'/api/generate/transcribe/jobs/{job_id}/artifact',
                    sha256=receipts['srt']['sha256'] if 'srt' in receipts else None,
                    bytes=receipts['srt']['bytes'] if 'srt' in receipts else None,
                    artifacts=receipts, text=result['text'],
                    language=result['language'],
                    media_duration_seconds=result['duration'],
                    segment_count=len(result['segments']))
                try:
                    self._persist()
                except Exception:
                    job.update(state='error', artifacts={}, output_path=None,
                               output_url=None, sha256=None, bytes=None,
                               error='Final artifact could not be persisted; reconcile before retry',
                               error_code='JOB_PERSISTENCE_UNCERTAIN')
                    try:
                        for path in staged:
                            path.unlink(missing_ok=True)
                        job.update(error='Final artifact could not be persisted; '
                                         'artifacts were discarded',
                                   error_code='JOB_PERSISTENCE_FAILED')
                    except OSError:
                        pass
                    raise
        except _Cancelled:
            with self.lock:
                job.update(state='cancelled', error=None, error_code=None,
                           finished_at=time.time())
                self._persist()
        except Exception as exc:
            with self.lock:
                if event.is_set():
                    job.update(state='cancelled', error=None, error_code=None)
                elif job.get('error_code') not in ('JOB_PERSISTENCE_FAILED',
                                                   'JOB_PERSISTENCE_UNCERTAIN'):
                    detail = str(exc) or type(exc).__name__
                    if isinstance(exc, FileNotFoundError):
                        code = 'INPUT_NOT_FOUND'
                    elif isinstance(exc, ModelPackageError):
                        code = 'MODEL_FILES_UNAVAILABLE'
                    elif isinstance(exc, ValueError):
                        code = 'INVALID_SUBTITLE_REQUEST'
                    elif isinstance(exc, RuntimeError):
                        code = 'TRANSCRIPTION_UNAVAILABLE'
                    else:
                        code = 'TRANSCRIPTION_FAILED'
                    job.update(state='error', error=detail[:500], error_code=code)
                job['finished_at'] = time.time()
                job['duration_seconds'] = round(job['finished_at'] - job['started_at'], 3)
                self._persist()
        finally:
            for path in staged:
                path.unlink(missing_ok=True)
            self.cancel_events.pop(job_id, None)

    def _progress(self, job_id: str, value: float) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is not None and job['state'] == 'running':
                job['progress'] = max(job.get('progress') or 0.0, float(value))

    def close(self, timeout: float = 60.0) -> None:
        """Wait for in-flight jobs so the owner never tears down state mid-write."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if not self.cancel_events:
                    return
            time.sleep(0.02)

    def _publish(self, job_id: str, result: dict, formats: list[str],
                 staged: list[Path]) -> dict:
        """Write final artifacts atomically; never publish a partial cue file."""
        contents = {'srt': result['srt'], 'vtt': result['vtt'],
                    'text': result['text_file']}
        receipts: dict[str, dict] = {}
        for fmt in formats:
            body = contents[fmt]
            if not str(body or '').strip():
                if fmt == 'text' and result['segments']:
                    raise ValueError('Text transcript is empty for a non-empty result')
                if fmt in ('srt', 'vtt') and result['segments']:
                    raise ValueError(f'{fmt.upper()} artifact would be empty')
            final = self.artifacts / f'{job_id}.{ARTIFACT_FORMATS[fmt][1]}'
            temporary = self.artifacts / f'.{job_id}.{ARTIFACT_FORMATS[fmt][1]}.part'
            staged.append(temporary)
            temporary.write_text(body, encoding='utf-8')
            os.replace(temporary, final)
            digest, size = _sha256_file(final)
            receipts[fmt] = {'path': str(final), 'bytes': size, 'sha256': digest,
                             'media_type': ARTIFACT_FORMATS[fmt][0]}
        return receipts
