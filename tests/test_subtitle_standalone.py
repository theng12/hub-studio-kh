"""Independent Subtitle service: contract, lifecycle and export tests.

The suite separates two kinds of proof:

* Contract/lifecycle cases use a deterministic fake decoder and a temporary
  library built with the ST-01 ``import_folder`` helper. They prove the
  service's own behavior, not model accuracy.
* ``SubtitleRealModelTests`` runs the real Whisper engine against the verified
  local packages and is skipped unless ``STUDIOHUB_TEST_MODEL_LIBRARY`` names
  a library. A skip is reported as a skip, never as a pass.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
import wave

import httpx
from starlette.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components import model_packages
from components.subtitle.app.backend import engine as engine_module
from components.subtitle.app.backend.main import create_app
from components.subtitle.app.backend.manager import SubtitleManager

TOKEN = 'subtitle-test-token-0123456789abcdef'
VOICE_APP = PROJECT_ROOT / 'components' / 'voice' / 'app'
REAL_LIBRARY = os.environ.get('STUDIOHUB_TEST_MODEL_LIBRARY', '').strip()
TURBO_REVISION = 'a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb'


def _write_tone(path: Path, seconds: float = 1.2, rate: int = 16000) -> bytes:
    """A small deterministic WAV so media probing and uploads are exercised."""
    frames = bytearray()
    total = int(seconds * rate)
    for index in range(total):
        burst = 1.0 if (index // 2000) % 2 == 0 else 0.0
        value = int(6000 * burst * math.sin(2 * math.pi * 220 * index / rate))
        frames += struct.pack('<h', value)
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return path.read_bytes()


class _FakeModel:
    """Deterministic mlx-audio-shaped result for lifecycle testing."""

    def __init__(self, segments, text='hello world'):
        self.segments = segments
        self.text = text
        self.language = 'en'
        self.calls: list[dict] = []

    def generate(self, audio, **options):
        self.calls.append({'audio': audio, **options})
        return self


def _fake_factory(segments, text='hello world'):
    holder: dict = {}

    def factory(location):
        model = _FakeModel([dict(segment) for segment in segments], text=text)
        holder['model'] = model
        return model
    return factory, holder


class SubtitleFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temporary.name)
        self.library = self.root / 'library'
        self.library.mkdir()
        self.data = self.root / 'state'
        self.audio = self.root / 'clip.wav'
        self.audio_bytes = _write_tone(self.audio)
        self.manifests = [self._package('whisper-turbo', 'weights.safetensors'),
                          self._package('whisper-base', 'weights.npz'),
                          self._package('whisper-small', 'weights.npz')]

    def tearDown(self) -> None:
        manager = getattr(self, 'manager', None)
        if manager is not None:
            manager.close()
        self.temporary.cleanup()

    def _package(self, package_id: str, weight_name: str) -> dict:
        revision = {
            'whisper-turbo': TURBO_REVISION,
            'whisper-base': '1e3e249fb8d01c655324bd6841b1deadffd6d04c',
            'whisper-small': '45f3915923c7a79a5a5b5a7d909d39aeb0e5630e',
        }[package_id]
        model_id = {'whisper-turbo': 'mlx-community/whisper-large-v3-turbo',
                    'whisper-base': 'mlx-community/whisper-base-mlx',
                    'whisper-small': 'mlx-community/whisper-small-mlx'}[package_id]
        source = self.root / f'source-{package_id}'
        (source / 'model').mkdir(parents=True, exist_ok=True)
        (source / 'companions' / 'processor').mkdir(parents=True, exist_ok=True)
        (source / 'model' / 'config.json').write_text('{"n_mels": 80}\n')
        (source / 'model' / weight_name).write_bytes(f'{package_id}-weights'.encode())
        for name in ('vocab.json', 'tokenizer.json', 'tokenizer_config.json'):
            (source / 'companions' / 'processor' / name).write_text('{}\n')
        files = []
        for path in sorted((source).rglob('*')):
            if not path.is_file():
                continue
            body = path.read_bytes()
            files.append({
                'path': path.relative_to(source).as_posix(),
                'size': len(body),
                'sha256': hashlib.sha256(body).hexdigest(),
                'urls': [f'https://example.invalid/{path.name}'],
            })
        manifest = {'schema_version': 1, 'package_id': package_id,
                    'model_id': model_id, 'revision': revision,
                    'engine': 'subtitle-mlx', 'minimum_macos': '14.0',
                    'files': files, 'source': {'provider': 'fixture'}}
        model_packages.import_folder(manifest, source, self.library)
        return manifest

    def build_manager(self, factory=None, segments=None, **kwargs) -> SubtitleManager:
        if factory is None:
            factory, _ = _fake_factory(segments or [
                {'start': 0.0, 'end': 0.5, 'text': 'hello world'},
                {'start': 0.6, 'end': 1.0, 'text': 'second cue'},
            ])
        manager = SubtitleManager(self.data, self.library, self.manifests, 'b' * 64,
                                  voice_app_dir=VOICE_APP, **kwargs)
        manager.engine.model_factory = factory
        return manager

    def wait(self, manager: SubtitleManager, job_id: str, timeout: float = 30.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = manager.get(job_id)
            if value and value['state'] in ('done', 'error', 'cancelled'):
                return value
            time.sleep(0.02)
        self.fail(f'job {job_id} did not reach a terminal state')

    def submit(self, manager: SubtitleManager, **overrides) -> dict:
        source = manager.store_input('test', 'clip.wav', self.audio_bytes)
        request = {'model': 'whisper-turbo', 'word_timestamps': False,
                   'formats': ['srt', 'vtt', 'text'], **overrides}
        return manager.submit(request, source)


class ExportSourceTests(SubtitleFixture):
    """ST-06.1 — one tracked source location for decoding and exports."""

    def test_export_behavior_comes_from_the_voice_source(self) -> None:
        manager = self.build_manager()
        receipt = manager.engine.export_source()
        path = Path(receipt['path'])
        self.assertEqual(path, VOICE_APP / 'backend' / 'transcription.py')
        self.assertTrue(path.is_file())
        self.assertEqual(receipt['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(manager.engine.voice_app_dir, VOICE_APP)

    def test_no_formatter_is_reimplemented_in_subtitle(self) -> None:
        """The Subtitle tree must not carry its own cue formatters."""
        for path in (PROJECT_ROOT / 'components' / 'subtitle').rglob('*.py'):
            body = path.read_text(encoding='utf-8')
            self.assertNotIn('-->', body,
                             f'{path} appears to build cue timestamps itself')
            self.assertNotIn('def segments_to_srt', body)
            self.assertNotIn('def segments_to_vtt', body)

    def test_exports_match_the_voice_functions_exactly(self) -> None:
        segments = [{'id': 0, 'start': 0.0, 'end': 1.5, 'text': 'First cue'},
                    {'id': 1, 'start': 2.0, 'end': 3.25, 'text': 'Second cue'}]
        artifacts = engine_module.format_artifacts(segments, VOICE_APP)
        from backend import transcription as voice
        self.assertEqual(artifacts['srt'], voice.segments_to_srt(segments))
        self.assertEqual(artifacts['vtt'], voice.segments_to_vtt(segments))
        self.assertTrue(artifacts['srt'].startswith('1\n00:00:00,000 --> 00:00:01,500'))
        self.assertTrue(artifacts['vtt'].startswith('WEBVTT'))
        self.assertEqual(artifacts['text'], 'First cue Second cue')

    def test_text_srt_vtt_are_published_together(self) -> None:
        manager = self.build_manager()
        job = self.submit(manager)
        value = self.wait(manager, job['id'])
        self.assertEqual(value['state'], 'done', value.get('error'))
        self.assertEqual(sorted(value['artifacts']), ['srt', 'text', 'vtt'])
        srt = Path(value['artifacts']['srt']['path']).read_text()
        vtt = Path(value['artifacts']['vtt']['path']).read_text()
        text = Path(value['artifacts']['text']['path']).read_text()
        self.assertIn('hello world', srt)
        self.assertTrue(vtt.startswith('WEBVTT'))
        self.assertEqual(text, 'hello world second cue')
        self.assertEqual(value['text'], 'hello world second cue')
        self.assertEqual(value['segment_count'], 2)


class CatalogContractTests(SubtitleFixture):
    """ST-06.4 — Base, Small and Turbo pinned, Turbo the recommended default."""

    def test_catalog_pins_the_three_whisper_models(self) -> None:
        manager = self.build_manager()
        entries = {entry['package_id']: entry for entry in manager.catalog()}
        self.assertEqual(sorted(entries), ['whisper-base', 'whisper-small', 'whisper-turbo'])
        self.assertEqual(entries['whisper-turbo']['model_revision'], TURBO_REVISION)
        self.assertTrue(entries['whisper-turbo']['recommended'])
        self.assertEqual(manager.default_package(), 'whisper-turbo')
        for entry in entries.values():
            self.assertEqual(entry['engine'], 'subtitle-mlx')
            self.assertEqual(entry['modality'], 'transcription')
            self.assertEqual(entry['hub_operation'], 'audio.transcription')
            self.assertEqual(entry['runtime_revision'], 'b' * 64)

    def test_npz_only_packages_are_reported_not_runnable(self) -> None:
        """Base/Small ship weights.npz, which this mlx-audio loader cannot read."""
        manager = self.build_manager()
        entries = {entry['package_id']: entry for entry in manager.catalog()}
        for package_id in ('whisper-base', 'whisper-small'):
            files = entries[package_id]['readiness']['files']
            self.assertEqual(files['state'], 'unsupported_weight_format')
            self.assertFalse(files['runtime_supported'])
            self.assertFalse(entries[package_id]['readiness']['ready'])
            self.assertIn('safetensors', entries[package_id]['readiness']['reason'])
        turbo = entries['whisper-turbo']['readiness']
        self.assertTrue(turbo['ready'])
        self.assertTrue(turbo['files']['runtime_supported'])

    def test_submitting_a_non_runnable_package_is_refused(self) -> None:
        manager = self.build_manager()
        source = manager.store_input('test', 'clip.wav', self.audio_bytes)
        with self.assertRaises(engine_module.ModelPackageError) as caught:
            manager.submit({'model': 'whisper-base', 'formats': ['srt']}, source)
        self.assertIn('safetensors', str(caught.exception))

    def test_unknown_or_unpackaged_model_is_refused(self) -> None:
        manager = self.build_manager()
        with self.assertRaises(engine_module.ModelPackageError):
            manager.engine.package('whisper-nonexistent')

    def test_request_validation_rejects_bad_input(self) -> None:
        from components.subtitle.app.backend.manager import validate_request
        packages = ['whisper-turbo']
        with self.assertRaises(ValueError):
            validate_request({'model': 'other', 'formats': ['srt']},
                             packages=packages, default_package='whisper-turbo')
        with self.assertRaises(ValueError):
            validate_request({'language': 'not a language!', 'formats': ['srt']},
                             packages=packages, default_package='whisper-turbo')
        with self.assertRaises(ValueError):
            validate_request({'word_timestamps': 'yes', 'formats': ['srt']},
                             packages=packages, default_package='whisper-turbo')
        with self.assertRaises(ValueError):
            validate_request({'formats': ['pdf']},
                             packages=packages, default_package='whisper-turbo')
        value = validate_request({'formats': ['vtt']},
                                 packages=packages, default_package='whisper-turbo')
        self.assertEqual(value['formats'], ['srt', 'vtt'])
        self.assertIsNone(value['language'])

    def test_media_extension_and_filename_guards(self) -> None:
        from components.subtitle.app.backend.manager import safe_input_name
        for name in ('clip.wav', 'a b (1).mp4', 'x.MOV'):
            self.assertEqual(safe_input_name(name), name)
        for name in ('', '..', '../escape.wav', 'a/b.wav', 'clip.txt', 'clip', 'bad\x00.wav'):
            with self.assertRaises(ValueError, msg=name):
                safe_input_name(name)


class DecodePathTests(SubtitleFixture):
    """The engine decodes through the pinned call the Voice source adopted."""

    def test_decode_uses_the_pinned_options_and_normalizes_output(self) -> None:
        factory, holder = _fake_factory([
            {'start': 0.0, 'end': 0.5, 'text': 'hello world'},
            {'start': 0.6, 'end': 99.0, 'text': 'past the media end'},
        ])
        manager = self.build_manager(factory=factory)
        job = self.submit(manager, word_timestamps=True)
        value = self.wait(manager, job['id'])
        self.assertEqual(value['state'], 'done', value.get('error'))
        call = holder['model'].calls[0]
        self.assertFalse(call['condition_on_previous_text'])
        self.assertTrue(call['return_timestamps'])
        self.assertTrue(call['word_timestamps'])
        self.assertIsNone(call['language'])
        # The Voice normalizer clamps the final cue to the real media duration.
        srt = Path(value['artifacts']['srt']['path']).read_text()
        self.assertNotIn('00:01:39', srt)
        self.assertLessEqual(value['media_duration_seconds'], 1.3)

    def test_language_is_forwarded(self) -> None:
        factory, holder = _fake_factory([{'start': 0.0, 'end': 0.5, 'text': 'hi'}])
        manager = self.build_manager(factory=factory)
        job = self.submit(manager, language='en')
        self.wait(manager, job['id'])
        self.assertEqual(holder['model'].calls[0]['language'], 'en')

    def test_missing_input_file_fails_the_job(self) -> None:
        manager = self.build_manager()
        source = manager.store_input('test', 'clip.wav', self.audio_bytes)
        Path(source['path']).unlink()
        job = manager.submit({'model': 'whisper-turbo', 'formats': ['srt']}, source)
        value = self.wait(manager, job['id'])
        self.assertEqual(value['state'], 'error')
        self.assertEqual(value['error_code'], 'INPUT_NOT_FOUND')


class LifecycleTests(SubtitleFixture):
    """ST-06.2 — independent jobs, cancellation, history and restart behavior."""

    def test_cancel_stops_the_job_and_publishes_nothing(self) -> None:
        manager = self.build_manager()
        job = self.submit(manager)
        manager.cancel(job['id'])
        value = self.wait(manager, job['id'])
        self.assertEqual(value['state'], 'cancelled')
        self.assertEqual(value['artifacts'], {})
        self.assertFalse(list((self.data / 'artifacts').glob('*')))

    def test_cancel_is_idempotent_and_unknown_ids_are_none(self) -> None:
        manager = self.build_manager()
        job = self.submit(manager)
        manager.cancel(job['id'])
        again = manager.cancel(job['id'])
        self.assertIn(again['state'], ('running', 'cancelled'))
        self.assertEqual(self.wait(manager, job['id'])['state'], 'cancelled')
        self.assertIsNone(manager.cancel('missing-job'))

    def test_history_is_separate_and_survives_restart(self) -> None:
        manager = self.build_manager()
        first = self.submit(manager)
        done = self.wait(manager, first['id'])
        self.assertEqual(done['state'], 'done')
        self.assertEqual(len(manager.list()), 1)

        # A second process opening the same state directory finds its own jobs.
        restarted = SubtitleManager(self.data, self.library, self.manifests, 'b' * 64,
                                    voice_app_dir=VOICE_APP)
        restored = restarted.get(first['id'])
        self.assertEqual(restored['state'], 'done')
        self.assertTrue(restarted._final_matches(restored))
        self.assertEqual(len(restarted.list()), 1)

    def test_restart_marks_in_flight_jobs_uncertain(self) -> None:
        manager = self.build_manager()
        job = self.submit(manager)
        # Simulate a crash after the queued receipt was written.
        state = json.loads((self.data / 'jobs.json').read_text())
        state[job['id']]['state'] = 'running'
        (self.data / 'jobs.json').write_text(json.dumps(state))
        restarted = SubtitleManager(self.data, self.library, self.manifests, 'b' * 64,
                                    voice_app_dir=VOICE_APP)
        value = restarted.get(job['id'])
        self.assertEqual(value['state'], 'error')
        self.assertEqual(value['error_code'], 'WORKER_RESTART_UNCERTAIN')

    def test_artifacts_without_a_receipt_are_uncertain_not_won(self) -> None:
        manager = self.build_manager()
        job = self.submit(manager)
        self.wait(manager, job['id'])
        state = json.loads((self.data / 'jobs.json').read_text())
        state[job['id']]['state'] = 'running'
        (self.data / 'jobs.json').write_text(json.dumps(state))
        restarted = SubtitleManager(self.data, self.library, self.manifests, 'b' * 64,
                                    voice_app_dir=VOICE_APP)
        value = restarted.get(job['id'])
        self.assertEqual(value['error_code'], 'JOB_PERSISTENCE_UNCERTAIN')

    def test_ack_is_idempotent_and_requires_real_bytes(self) -> None:
        manager = self.build_manager()
        job = self.submit(manager)
        value = self.wait(manager, job['id'])
        receipt = manager.ack(job['id'])
        self.assertTrue(receipt['ok'])
        self.assertEqual(receipt['sha256'], value['sha256'])
        self.assertEqual(manager.ack(job['id'])['sha256'], receipt['sha256'])
        self.assertTrue(manager.get(job['id'])['acknowledged'])

        Path(value['artifacts']['srt']['path']).write_text('tampered\n')
        self.assertIsNone(manager.ack(job['id']))
        self.assertIsNone(manager.artifact_path(job['id'], 'srt'))

    def test_ack_rejects_unfinished_jobs(self) -> None:
        manager = self.build_manager()
        job = self.submit(manager)
        self.assertIsNone(manager.ack(job['id']))
        self.assertIsNone(manager.artifact_path(job['id'], 'srt'))
        self.assertIsNone(manager.artifact_path(job['id'], 'pdf'))


class OwnerTokenTests(SubtitleFixture):
    """The private worker refuses anything without the owning Hub credential."""

    def test_create_app_requires_a_token(self) -> None:
        manager = self.build_manager()
        with self.assertRaises(ValueError):
            create_app(manager, '')

    def test_every_endpoint_rejects_a_foreign_or_missing_token(self) -> None:
        manager = self.build_manager()
        client = TestClient(create_app(manager, TOKEN))
        for headers in ({}, {'X-Hub-Token': 'wrong'}, {'Authorization': 'Bearer nope'}):
            response = client.get('/api/health', headers=headers)
            self.assertEqual(response.status_code, 401, headers)
        self.assertEqual(client.get('/api/health', headers={'X-Hub-Token': TOKEN}).status_code, 200)


class SubtitleApiTests(SubtitleFixture):
    """ST-06.1/2 — the public Subtitle endpoints on a private loopback app."""

    def setUp(self) -> None:
        super().setUp()
        self.manager = self.build_manager()
        self.manager.engine.offline = True
        self.client = TestClient(create_app(self.manager, TOKEN))
        self.headers = {'X-Hub-Token': TOKEN}

    def _upload(self, url: str, data: dict, headers: dict | None = None):
        body = dict(data)
        body.setdefault('files', None)
        return self.client.post(
            url, headers=headers or self.headers, data=body,
            files={'file': ('clip.wav', io.BytesIO(self.audio_bytes), 'audio/wav')})

    def test_health_reports_busy_state_and_runtime_identity(self) -> None:
        payload = self.client.get('/api/health', headers=self.headers).json()
        self.assertTrue(payload['ok'])
        self.assertFalse(payload['transcription']['busy'])
        self.assertEqual(payload['transcription']['runtime_revision'], 'b' * 64)

    def test_catalog_and_availability_expose_the_same_models(self) -> None:
        catalog = self.client.get('/api/catalog', headers=self.headers).json()
        self.assertEqual(catalog['default_model'], 'whisper-turbo')
        self.assertEqual(len(catalog['models']), 3)
        availability = self.client.get('/api/transcribe/availability',
                                       headers=self.headers).json()
        self.assertEqual(availability['default_model'], 'whisper-turbo')
        self.assertTrue(availability['available'])
        self.assertEqual(len(availability['models']), 3)
        repos = {model['repo'] for model in availability['models']}
        self.assertIn('mlx-community/whisper-large-v3-turbo', repos)

    def test_hub_transcription_contract_returns_srt_and_identity(self) -> None:
        response = self._upload('/api/transcribe',
                                {'model': 'whisper-turbo', 'language': 'en',
                                 'word_timestamps': 'false', 'activity_id': 'stt-hub-1'})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn('00:00:00,000 -->', payload['srt'])
        self.assertTrue(payload['vtt'].startswith('WEBVTT'))
        self.assertEqual(payload['text'], 'hello world second cue')
        self.assertEqual(payload['studio_task_id'], payload['task_id'])
        self.assertEqual(payload['model_revision'], TURBO_REVISION)
        self.assertEqual(payload['package_id'], 'whisper-turbo')
        job = self.manager.get(payload['job_id'])
        self.assertEqual(job['activity_id'], 'stt-hub-1')
        self.assertEqual(job['state'], 'done')

    def test_hub_transcription_refuses_a_non_runnable_model(self) -> None:
        response = self._upload('/api/transcribe', {'model': 'whisper-base'})
        self.assertEqual(response.status_code, 409)
        self.assertIn('safetensors', response.text)

    def test_hub_transcription_rejects_a_foreign_extension(self) -> None:
        response = self.client.post(
            '/api/transcribe', headers=self.headers, data={'model': 'whisper-turbo'},
            files={'file': ('notes.txt', io.BytesIO(b'hello'), 'text/plain')})
        self.assertEqual(response.status_code, 422)

    def test_studio_submit_status_cancel_and_artifact(self) -> None:
        response = self._upload('/api/generate/transcribe',
                                {'model': 'whisper-turbo', 'formats': 'srt,vtt'})
        self.assertEqual(response.status_code, 200, response.text)
        job_id = response.json()['job']['id']
        value = self.wait(self.manager, job_id)
        self.assertEqual(value['state'], 'done')
        self.assertNotIn('input', response.json()['job'])

        detail = self.client.get(f'/api/generate/transcribe/jobs/{job_id}',
                                 headers=self.headers).json()['job']
        self.assertEqual(detail['state'], 'done')
        self.assertGreater(detail['artifacts']['srt']['bytes'], 0)
        self.assertEqual(len(detail['artifacts']['srt']['sha256']), 64)

        listing = self.client.get('/api/generate/transcribe/jobs',
                                  headers=self.headers).json()['jobs']
        self.assertEqual([item['id'] for item in listing], [job_id])

        artifact = self.client.get(
            f'/api/generate/transcribe/jobs/{job_id}/artifact?format=vtt',
            headers=self.headers)
        self.assertEqual(artifact.status_code, 200)
        self.assertTrue(artifact.headers['content-type'].startswith('text/vtt'))
        self.assertTrue(artifact.text.startswith('WEBVTT'))
        self.assertEqual(artifact.headers['x-content-sha256'],
                         value['artifacts']['vtt']['sha256'])
        self.assertEqual(self.client.get(
            f'/api/generate/transcribe/jobs/{job_id}/artifact?format=pdf',
            headers=self.headers).status_code, 404)

        ack = self.client.post(f'/api/generate/transcribe/jobs/{job_id}/ack',
                               headers=self.headers)
        self.assertEqual(ack.status_code, 200)
        self.assertTrue(ack.json()['ok'])
        self.assertEqual(self.client.post(
            f'/api/generate/transcribe/jobs/{job_id}/ack',
            headers=self.headers).json()['sha256'], ack.json()['sha256'])

        cancel = self.client.delete(f'/api/generate/transcribe/jobs/{job_id}',
                                    headers=self.headers)
        self.assertEqual(cancel.status_code, 200)
        self.assertEqual(cancel.json()['job']['state'], 'done')

    def test_studio_submit_requires_a_file(self) -> None:
        response = self.client.post('/api/generate/transcribe', headers=self.headers,
                                    data={'model': 'whisper-turbo'})
        self.assertEqual(response.status_code, 422)

    def test_unknown_job_returns_404(self) -> None:
        self.assertEqual(self.client.get('/api/generate/transcribe/jobs/nope',
                                         headers=self.headers).status_code, 404)
        self.assertEqual(self.client.delete('/api/generate/transcribe/jobs/nope',
                                            headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get(
            '/api/generate/transcribe/jobs/nope/artifact', headers=self.headers).status_code, 404)

    def test_uploads_never_overwrite_each_other(self) -> None:
        first = self._upload('/api/generate/transcribe', {'model': 'whisper-turbo'})
        second = self._upload('/api/generate/transcribe', {'model': 'whisper-turbo'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_job = first.json()['job']['id']
        second_job = second.json()['job']['id']
        self.assertNotEqual(first_job, second_job)
        first_value = self.manager.get(first_job)
        second_value = self.manager.get(second_job)
        self.assertNotEqual(first_value['input']['sha256'] + first_job,
                            second_value['input']['sha256'] + second_job)
        stored = sorted(path.name for path in (self.data / 'inputs').iterdir())
        self.assertEqual(len(stored), 2)
        self.assertNotEqual(stored[0], stored[1])
        for job_id in (first_job, second_job):
            value = self.manager.get(job_id)
            self.assertTrue(Path(value['input']['original_path']).name.startswith('original'))


@unittest.skipUnless(REAL_LIBRARY, 'set STUDIOHUB_TEST_MODEL_LIBRARY to run real Whisper')
class SubtitleRealModelTests(unittest.TestCase):
    """Real decoding against the verified packages. Skipped without a library."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.library = Path(REAL_LIBRARY)
        cls.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.root = Path(cls.temporary.name)
        cls.manifests = []
        for name in ('whisper-turbo.json', 'whisper-base.json', 'whisper-small.json'):
            path = PROJECT_ROOT / 'model-manifests' / name
            if path.is_file():
                cls.manifests.append(json.loads(path.read_text()))
        cls.audio = cls.root / 'tone.wav'
        _write_tone(cls.audio, seconds=1.0)
        cls.manager = SubtitleManager(cls.root / 'state', cls.library, cls.manifests,
                                      'c' * 64, voice_app_dir=VOICE_APP)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.manager.engine.unload('test-teardown')
        cls.temporary.cleanup()

    def _run(self, **overrides) -> dict:
        source = self.manager.store_input('real', 'tone.wav', self.audio.read_bytes())
        request = {'model': 'whisper-turbo', 'formats': ['srt', 'vtt', 'text'],
                   **overrides}
        job = self.manager.submit(request, source)
        return self._wait(job['id'])

    def test_turbo_transcribes_and_exports_from_the_verified_package(self) -> None:
        readiness = self.manager.readiness('whisper-turbo')
        self.assertTrue(readiness['ready'], readiness['reason'])
        value = self._run()
        self.assertEqual(value['state'], 'done', value.get('error'))
        self.assertEqual(value['package_id'], 'whisper-turbo')
        self.assertEqual(value['model_revision'], TURBO_REVISION)
        self.assertIsNotNone(value['media_duration_seconds'])
        self.assertGreater(value['media_duration_seconds'], 0)
        srt = Path(value['artifacts']['srt']['path']).read_text()
        self.assertTrue(srt.strip())
        self.assertRegex(srt, r'^\d+\n\d{2}:\d{2}:\d{2},\d{3} --> ')
        self.assertTrue(Path(value['artifacts']['vtt']['path']).read_text().startswith('WEBVTT'))
        self.assertIsNotNone(self.manager.ack(value['id']))

    def test_base_and_small_are_not_claimed_as_runnable(self) -> None:
        for package_id in ('whisper-base', 'whisper-small'):
            readiness = self.manager.readiness(package_id)
            self.assertFalse(readiness['ready'], package_id)
            self.assertEqual(readiness['files']['state'], 'unsupported_weight_format')

    def test_cancel_during_real_decoding_publishes_no_artifact(self) -> None:
        source = self.manager.store_input('real', 'tone.wav', self.audio.read_bytes())
        job = self.manager.submit(
            {'model': 'whisper-turbo', 'formats': ['srt', 'vtt', 'text']}, source)
        # Wait until decoding actually started, then cancel mid-flight.
        deadline = time.time() + 120
        while time.time() < deadline:
            value = self.manager.get(job['id'])
            if value['state'] == 'running' and value.get('progress', 0) >= 0.1:
                break
            time.sleep(0.05)
        self.manager.cancel(job['id'])
        value = self._wait(job['id'])
        self.assertEqual(value['state'], 'cancelled', value.get('error'))
        self.assertEqual(value['artifacts'], {})
        self.assertFalse((self.manager.artifacts / f"{job['id']}.srt").exists())

    def test_verified_package_tree_is_never_modified_by_decoding(self) -> None:
        """Loading must not disturb the pinned package the ST-01 receipt covers.

        Regression: attaching the tokenizer straight from the package folder
        bumps every file's ctime, so ST-01's preflight reports
        "File changed; verify again" after each real transcription. The engine
        stages the verified companion tokenizer into Subtitle's own state.
        """
        location = self.manager.engine.package('whisper-turbo')

        def metadata() -> dict:
            return {str(path.relative_to(location.folder)):
                    (path.stat().st_ino, path.stat().st_mtime_ns,
                     path.stat().st_ctime_ns, path.stat().st_size)
                    for path in sorted(location.folder.rglob('*')) if path.is_file()}

        before = metadata()
        self._run()
        self.assertEqual(before, metadata())
        self.assertEqual(self.manager.readiness('whisper-turbo')['files']['state'],
                         'files_verified')
        # The tokenizer really was staged privately, not read in place.
        processor = self.manager.data / 'processor' / 'whisper-turbo' / location.revision
        self.assertTrue((processor / 'tokenizer.json').is_file())

    def _wait(self, job_id: str, timeout: float = 600) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = self.manager.get(job_id)
            if value['state'] in ('done', 'error', 'cancelled'):
                return value
            time.sleep(0.1)
        self.fail('real Whisper job did not finish within 600s')


class SubtitleModelIdentifierTests(unittest.TestCase):
    """The Hub's transcription contract identifies a model by repository.

    The worker keys its catalogue by package id. A packaged Subtitle job was answered with
    ``422 choose one of the packaged Whisper models`` because only the package id was accepted,
    so both spellings are pinned here.
    """

    aliases = {'mlx-community/whisper-large-v3-turbo': 'whisper-turbo'}

    def test_model_repository_resolves_to_its_package(self) -> None:
        from components.subtitle.app.backend import manager as subtitle_manager

        request = subtitle_manager.validate_request(
            {'model': 'mlx-community/whisper-large-v3-turbo', 'language': 'en',
             'word_timestamps': False, 'formats': ['srt']},
            packages=['whisper-turbo'], default_package='whisper-turbo',
            aliases=self.aliases)
        self.assertEqual(request['model'], 'whisper-turbo')

    def test_package_id_still_resolves_to_itself(self) -> None:
        from components.subtitle.app.backend import manager as subtitle_manager

        request = subtitle_manager.validate_request(
            {'model': 'whisper-turbo', 'formats': ['srt']},
            packages=['whisper-turbo'], default_package='whisper-turbo',
            aliases=self.aliases)
        self.assertEqual(request['model'], 'whisper-turbo')

    def test_an_unpackaged_model_is_still_refused(self) -> None:
        from components.subtitle.app.backend import manager as subtitle_manager

        with self.assertRaisesRegex(ValueError, 'packaged Whisper models'):
            subtitle_manager.validate_request(
                {'model': 'someone/unknown-whisper', 'formats': ['srt']},
                packages=['whisper-turbo'], default_package='whisper-turbo',
                aliases=self.aliases)

    def test_engine_publishes_repository_aliases(self) -> None:
        """The alias map must be derived from the real manifests, not hard-coded."""
        import inspect

        from components.subtitle.app.backend import manager as subtitle_manager

        source = inspect.getsource(subtitle_manager.SubtitleManager.model_aliases)
        self.assertIn('model_id', source)
        self.assertIn('manifests', source)


class SubtitleMemoryPolicyTests(unittest.TestCase):
    """The Subtitle worker must not hold a Whisper model resident forever.

    Voice has an idle-release policy; Subtitle had ``engine.unload()`` with nothing calling
    it, so a model could stay in memory for the process lifetime — the leak that matters most
    on an 8 GB machine.
    """

    def test_modes_match_the_voice_worker(self) -> None:
        from components.subtitle.app.backend import memory_policy

        self.assertEqual(set(memory_policy.MODES),
                         {'performance', 'balanced', 'memory_saver', 'immediate'})
        self.assertIsNone(memory_policy.MODES['performance']['idle_seconds'])
        self.assertEqual(memory_policy.MODES['immediate']['idle_seconds'], 0)

    def test_small_machines_default_to_immediate_release(self) -> None:
        from components.subtitle.app.backend import memory_policy

        original = memory_policy.machine_bytes
        try:
            memory_policy.machine_bytes = lambda: 8 * 1024**3
            self.assertEqual(memory_policy.default_mode(), 'immediate')
            memory_policy.machine_bytes = lambda: 32 * 1024**3
            self.assertEqual(memory_policy.default_mode(), 'balanced')
        finally:
            memory_policy.machine_bytes = original

    def test_an_operator_choice_beats_the_machine_default(self) -> None:
        import tempfile
        from pathlib import Path
        from components.subtitle.app.backend import memory_policy

        with tempfile.TemporaryDirectory() as temporary:
            original = memory_policy.settings_file
            memory_policy.settings_file = lambda: Path(temporary) / 'memory_policy.json'
            try:
                self.assertIn(memory_policy.save('memory_saver')['mode'], {'memory_saver'})
                self.assertEqual(memory_policy.status()['idle_seconds'], 120)
                with self.assertRaises(ValueError):
                    memory_policy.save('bogus')
            finally:
                memory_policy.settings_file = original

    def test_active_work_is_never_released(self) -> None:
        from unittest.mock import patch
        from components.subtitle.app.backend import memory_policy

        with patch.object(memory_policy, '_active', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'active'):
                memory_policy.release_now('idle')


if __name__ == '__main__':
    unittest.main()
